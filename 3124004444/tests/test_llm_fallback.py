"""大模型模式的「无 Key 也不崩」测试。

真实的 API 调用不在测试范围内（需要 Key 且不稳定），
但下面这些边界必须验证：
- 没配 Key 时自动退回规则式规划器
- Key 填错/网络挂了时，本轮降级而不是抛异常
- 工具清单能正确转成 OpenAI function calling 格式
- 模型返回的非法 JSON 参数能被宽松修复
"""

from __future__ import annotations

import pytest

from agent.config import Settings, get_settings, use_settings
from agent.contracts import ToolResult
from agent.llm.client import LLMError, OpenAICompatClient
from agent.llm.tools_format import parse_tool_calls, to_openai_tools
from agent.planner.base import PlanContext
from agent.planner.llm_planner import LLMPlanner
from agent.registry import get_registry


def _settings(**overrides) -> Settings:
    base = Settings(agent_mode="llm", mcp_channel="builtin")
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture()
def tools():
    return get_registry().all()


def test_没有_key_自动退回规则式(tools) -> None:
    planner = LLMPlanner(_settings(llm_api_key=""))
    decision = planner.plan(PlanContext(user_text="算一下 1+1", tools=tools))
    assert decision.calls
    assert decision.calls[0].name == "calculate"
    assert planner.last_error


def test_调用失败时降级并在思考里标注(tools, monkeypatch) -> None:
    planner = LLMPlanner(_settings(llm_api_key="fake-key"))

    def boom(*_args, **_kwargs):
        raise LLMError("模拟 401")

    monkeypatch.setattr(planner.client, "chat", boom)
    decision = planner.plan(PlanContext(user_text="北京天气", tools=tools))

    assert decision.calls
    assert decision.calls[0].name == "get_weather"
    assert "大模型不可用" in decision.thought
    assert decision.debug.get("llm_error")


def test_工具转成_openai_格式(tools) -> None:
    converted = to_openai_tools(tools)
    assert len(converted) == len(tools)
    first = converted[0]
    assert first["type"] == "function"
    assert set(first["function"]) == {"name", "description", "parameters"}
    assert first["function"]["parameters"]["type"] == "object"


def test_解析模型返回的工具调用() -> None:
    message = {
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "calculate", "arguments": '{"expression": "1+1"}'},
            }
        ],
    }
    calls = parse_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].name == "calculate"
    assert calls[0].arguments == {"expression": "1+1"}
    assert calls[0].call_id == "call_abc"


def test_非法_json_参数被宽松修复() -> None:
    message = {
        "tool_calls": [
            {
                "id": "x",
                "function": {"name": "get_weather", "arguments": '{"city": "北京",}'},
            }
        ]
    }
    calls = parse_tool_calls(message)
    assert calls[0].arguments == {"city": "北京"}


def test_完全无法解析时给空参数() -> None:
    message = {"tool_calls": [{"id": "x", "function": {"name": "todo", "arguments": "not json"}}]}
    calls = parse_tool_calls(message)
    assert calls[0].arguments == {}


def test_没有工具调用时返回文本(tools, monkeypatch) -> None:
    planner = LLMPlanner(_settings(llm_api_key="fake-key"))
    monkeypatch.setattr(
        planner.client,
        "chat",
        lambda *_args, **_kwargs: {"content": "本龙今天心情很好～"},
    )
    decision = planner.plan(PlanContext(user_text="随便聊聊", tools=tools))
    assert decision.final_text == "本龙今天心情很好～"
    assert not decision.calls


def test_客户端未配置时给出明确错误() -> None:
    client = OpenAICompatClient(_settings(llm_api_key=""))
    assert client.configured is False
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------- 配置生效
def test_use_settings_让离线开关对工具生效() -> None:
    before = get_settings()
    try:
        use_settings(Settings(offline=True))
        from agent.net import is_offline

        assert is_offline() is True
        use_settings(Settings(offline=False))
        assert is_offline() is False
    finally:
        use_settings(before)


def test_tool_name_在结果中传递() -> None:
    result = ToolResult.success("ok")
    result.tool_name = "calculate"
    assert ToolResult.from_json(result.to_json()).tool_name == "calculate"
