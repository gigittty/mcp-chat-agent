"""工具契约测试。

这一组测试不关心工具「算得对不对」，只关心它们是否守规矩：
schema 合法、名字唯一、空参数不崩、返回值类型正确。
新工具加入 ``TOOL_MODULES`` 后会自动被覆盖到。
"""

from __future__ import annotations

import re

import pytest

from agent.contracts import ToolResult
from agent.registry import get_registry

SNAKE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


@pytest.fixture(scope="module")
def specs():
    return get_registry().all()


def test_至少有一个工具(specs) -> None:
    assert specs, "registry 里一个工具都没有"


def test_工具名唯一且合法(specs) -> None:
    names = [spec.name for spec in specs]
    assert len(names) == len(set(names)), f"工具名重复：{names}"
    for name in names:
        assert SNAKE.match(name), f"不是 snake_case：{name}"


def test_schema_根节点必须是_object(specs) -> None:
    # MCP 2.x 对 tools/list 做严格校验，根节点不是 object 会直接失败
    for spec in specs:
        assert spec.input_schema.get("type") == "object", f"{spec.name} 的 schema 根节点不是 object"


def test_required_字段都有声明(specs) -> None:
    for spec in specs:
        props = spec.input_schema.get("properties", {})
        for key in spec.input_schema.get("required", []):
            assert key in props, f"{spec.name}: required 里的 {key} 没有 properties 定义"


def test_每个工具都有描述(specs) -> None:
    for spec in specs:
        assert spec.title, f"{spec.name} 缺少中文名"
        assert len(spec.description) >= 15, f"{spec.name} 的描述太短，规划器难以判断何时调用"


def test_缺必填参数时返回失败而不是抛异常(specs) -> None:
    """工具是「返回失败」而不是「抛异常」，这条保证规划器不会把进程搞崩。"""
    for spec in specs:
        result = get_registry().invoke(spec.name, {})
        assert isinstance(result, ToolResult)
        if spec.required_params():
            assert result.ok is False, f"{spec.name} 缺必填参数却成功了"
            assert result.error, f"{spec.name} 失败时没有给出错误说明"


def test_非法参数类型不抛异常(specs) -> None:
    for spec in specs:
        result = get_registry().invoke(spec.name, {"__unexpected__": object()})
        assert isinstance(result, ToolResult)


def test_未知工具给出可用列表() -> None:
    result = get_registry().invoke("no_such_tool", {})
    assert result.ok is False
    assert result.hint
    assert "可用工具" in result.hint


def test_结果可序列化(specs) -> None:
    """内置通道与 MCP 通道共用同一序列化格式，必须能转成 JSON 字符串。"""
    for spec in specs:
        result = get_registry().invoke(spec.name, {})
        raw = result.to_json()
        assert isinstance(raw, str)
        restored = ToolResult.from_json(raw)
        assert restored.ok == result.ok
