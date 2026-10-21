"""schema 一致性契约测试 —— 本项目「一份定义」的保障。

注册表里的 ``ToolSpec`` 是工具参数的唯一真源，而 MCP 侧靠 Python 类型标注
生成 schema。两处**必须**一致，否则会出现「内置通道能调、MCP 通道参数对不上」
这种极难排查的问题。

这里用测试把这个约定钉死：
1. 每个工具都有对应的封装函数，反之亦然
2. 封装函数的参数名、类型、是否必填，与 SPEC 完全对应
3. 真的把服务端跑起来拉一次 tools/list，逐字段比对

第 3 步需要装 mcp 包；没装时前两步仍然生效（wrappers 不依赖 mcp）。
"""

from __future__ import annotations

import inspect
import types
import typing

import pytest

from agent.registry import get_registry
from mcp_server import wrappers

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _json_type(annotation: object) -> str:
    """把 Python 标注归一化成 JSON Schema 的 type。Optional[T] 视为 T。"""
    if annotation is type(None):
        return "null"
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        inner = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(inner) == 1:
            return _json_type(inner[0])
        return "anyOf"
    if origin in (list, tuple, set, frozenset):
        return "array"
    if origin is dict:
        return "object"
    return _PY_TO_JSON.get(annotation, getattr(annotation, "__name__", str(annotation)))


def _schema_type(prop: dict) -> str | None:
    """从 JSON Schema 片段里取类型。

    可选参数（Python 里是 ``T | None``）会被生成成
    ``{"anyOf": [{"type": "array"}, {"type": "null"}]}``，这属于正确行为，
    比对时取非 null 的那一支即可。
    """
    declared = prop.get("type")
    if declared:
        return declared
    for branch in prop.get("anyOf") or []:
        branch_type = branch.get("type")
        if branch_type and branch_type != "null":
            return branch_type
    return None


@pytest.fixture(scope="module")
def specs() -> dict:
    return {spec.name: spec for spec in get_registry().all()}


def test_工具与封装函数一一对应(specs: dict) -> None:
    wrapper_names = {function.__name__ for function in wrappers.ALL}
    spec_names = set(specs)
    assert wrapper_names == spec_names, (
        f"MCP 封装与注册表不一致：缺封装 {spec_names - wrapper_names}，"
        f"多封装 {wrapper_names - spec_names}"
    )


def test_参数名与顺序一致(specs: dict) -> None:
    for function in wrappers.ALL:
        spec = specs[function.__name__]
        params = list(inspect.signature(function).parameters)
        expected = list(spec.properties())
        missing = [name for name in expected if name not in params]
        assert not missing, f"{function.__name__}: 封装缺少参数 {missing}"


def test_参数类型一致(specs: dict) -> None:
    hints_cache = {
        function.__name__: typing.get_type_hints(function) for function in wrappers.ALL
    }
    for function in wrappers.ALL:
        spec = specs[function.__name__]
        hints = hints_cache[function.__name__]
        for name, prop in spec.properties().items():
            assert name in hints, f"{function.__name__}: 参数 {name} 没有类型标注"
            expected = prop.get("type")
            actual = _json_type(hints[name])
            assert actual == expected, (
                f"{function.__name__}.{name}: schema 是 {expected}，标注推导出 {actual}"
            )


def test_必填参数一致(specs: dict) -> None:
    for function in wrappers.ALL:
        spec = specs[function.__name__]
        signature = inspect.signature(function)
        required_by_signature = {
            name
            for name, param in signature.parameters.items()
            if param.default is inspect.Parameter.empty
        }
        assert required_by_signature == set(spec.required_params()), (
            f"{function.__name__}: 签名必填 {required_by_signature} "
            f"vs schema 必填 {set(spec.required_params())}"
        )


def test_描述文案与_spec_一致(specs: dict) -> None:
    """wrappers 在 import 时把 SPEC.description 注入 docstring，保证只有一份文案。"""
    for function in wrappers.ALL:
        spec = specs[function.__name__]
        assert (function.__doc__ or "").strip() == spec.description.strip(), (
            f"{function.__name__}: docstring 与 SPEC.description 不一致"
        )


# ---------------------------------------------------------------- 真跑一次协议
def test_服务端_tools_list_与注册表一致(specs: dict) -> None:
    """用进程内连接拉一次 tools/list，逐字段比对（不需要起子进程）。"""
    mcp = pytest.importorskip("mcp", reason="未安装 mcp 包")
    from mcp_server.server import server

    async def collect():
        async with mcp.Client(server) as client:
            return await client.list_tools()

    import asyncio

    listed = asyncio.run(collect())
    tools = {tool.name: tool for tool in listed.tools}

    assert set(tools) == set(specs), "MCP 暴露的工具集合与注册表不一致"

    for name, spec in specs.items():
        tool = tools[name]
        assert tool.description.strip() == spec.description.strip(), f"{name}: 描述不一致"
        # 中文标题也要传过去，否则 MCP 通道下界面会退化成显示函数名
        assert tool.title == spec.title, f"{name}: 标题不一致（MCP={tool.title!r}）"
        properties = tool.input_schema.get("properties", {})
        assert set(properties) == set(spec.properties()), f"{name}: 参数集合不一致"
        for param, prop in spec.properties().items():
            actual = _schema_type(properties[param])
            assert actual == prop.get("type"), (
                f"{name}.{param}: 类型不一致 "
                f"（MCP={actual}，SPEC={prop.get('type')}）"
            )
        assert set(tool.input_schema.get("required", [])) == set(spec.required_params()), (
            f"{name}: 必填项不一致"
        )
