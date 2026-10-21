"""Agent 主循环的测试。

用假的规划器（FakePlanner）驱动，验证主循环本身的行为：
多步执行、错误自纠、步数上限、$ref 解析、trace 落盘。
这层测试不关心「听不听得懂人话」，那是规划器的测试。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.agent import Agent, resolve_refs
from agent.config import Settings
from agent.contracts import PlanDecision, ToolCall, ToolResult
from agent.mcp_host.channel import ToolChannel
from agent.memory import Memory
from agent.planner.base import PlanContext, Planner


class FakePlanner(Planner):
    """按预设脚本逐轮返回决定，并记下每次被调用时的上下文快照。

    注意不能直接存 ``ctx``：``observations`` 是同一个列表对象，
    主循环会继续往里追加，事后再读就失真了。所以当场把长度和最后一条的
    状态快照下来。
    """

    name = "fake"

    def __init__(self, script: list[PlanDecision]) -> None:
        self.script = script
        self.snapshots: list[dict] = []

    def plan(self, ctx: PlanContext) -> PlanDecision:
        self.snapshots.append(
            {
                "step": ctx.step,
                "count": len(ctx.observations),
                "last_ok": ctx.observations[-1].ok if ctx.observations else None,
                "last_tool": ctx.last_tool,
            }
        )
        index = min(ctx.step, len(self.script) - 1)
        return self.script[index]


@pytest.fixture()
def settings() -> Settings:
    return Settings(agent_mode="mock", mcp_channel="builtin", max_steps=6)


def make_agent(script: list[PlanDecision], settings: Settings, max_steps: int | None = None) -> Agent:
    channel = ToolChannel("builtin")
    return Agent(
        planner=FakePlanner(script),
        channel=channel,
        memory=Memory(),
        settings=settings,
        max_steps=max_steps or settings.max_steps,
    )


def test_单步执行后给出最终回复(settings: Settings, data_root: Path) -> None:
    script = [
        PlanDecision(
            thought="算一下",
            calls=[ToolCall(name="calculate", arguments={"expression": "1+1"})],
        ),
        PlanDecision(thought="总结", final_text="算好了，是 2"),
    ]
    agent = make_agent(script, settings)
    result = agent.chat("算一下 1+1")

    assert result.kind == "final"
    assert result.reply == "算好了，是 2"
    assert len(result.observations) == 1
    assert result.observations[0].ok
    assert result.observations[0].data["value"] == 2


def test_多步按真实结果推进(settings: Settings, data_root: Path) -> None:
    """第二步必须能看到第一步的结果——这是「真多步」的判据。"""
    script = [
        PlanDecision(calls=[ToolCall(name="calculate", arguments={"expression": "3*3"})]),
        PlanDecision(
            calls=[ToolCall(name="make_ascii_art", arguments={"text": "OK"})],
        ),
        PlanDecision(final_text="全部完成"),
    ]
    agent = make_agent(script, settings)
    result = agent.chat("算一下 3*3 再做个字符画")

    assert result.kind == "final"
    assert [item.ok for item in result.observations] == [True, True]
    planner = agent.planner
    assert isinstance(planner, FakePlanner)
    # 三次规划时，规划器看到的观察数量依次是 0、1、2（真的逐步推进）
    assert [snap["count"] for snap in planner.snapshots] == [0, 1, 2]


def test_工具失败后回到规划器自纠(settings: Settings, data_root: Path) -> None:
    script = [
        PlanDecision(calls=[ToolCall(name="calculate", arguments={"expression": "1/0"})]),
        PlanDecision(calls=[ToolCall(name="calculate", arguments={"expression": "1+1"})]),
        PlanDecision(final_text="修好了"),
    ]
    agent = make_agent(script, settings)
    result = agent.chat("算一下 1/0")

    assert result.kind == "final"
    assert result.observations[0].ok is False
    assert result.observations[1].ok is True
    planner = agent.planner
    assert isinstance(planner, FakePlanner)
    # 自纠那一轮，规划器能看到上一步是失败的
    assert planner.snapshots[1]["count"] == 1
    assert planner.snapshots[1]["last_ok"] is False


def test_达到步数上限会停下(settings: Settings, data_root: Path) -> None:
    always_call = PlanDecision(calls=[ToolCall(name="calculate", arguments={"expression": "1+1"})])
    agent = make_agent([always_call], settings, max_steps=3)
    result = agent.chat("一直算")

    assert result.kind == "max_steps"
    assert len(result.observations) == 3
    assert result.reply


def test_反问会记下待补槽位(settings: Settings, data_root: Path) -> None:
    decision = PlanDecision(
        ask_user="哪个城市？",
        debug={"pending": {"name": "get_weather", "args": {}, "missing": ["city"]}},
    )
    agent = make_agent([decision], settings)
    result = agent.chat("天气怎么样")

    assert result.kind == "ask"
    assert agent.memory.pending is not None
    assert agent.memory.pending["missing"] == ["city"]


def test_工具抛异常不会让对话崩掉(settings: Settings, data_root: Path) -> None:
    class Boom(ToolChannel):
        def invoke(self, name, arguments):  # type: ignore[override]
            raise RuntimeError("模拟通道故障")

    agent = Agent(
        planner=FakePlanner([
            PlanDecision(calls=[ToolCall(name="calculate", arguments={"expression": "1+1"})]),
            PlanDecision(final_text="出错了但还活着"),
        ]),
        channel=Boom("builtin"),
        memory=Memory(),
        settings=settings,
    )
    result = agent.chat("算一下")
    assert result.kind == "final"
    assert result.observations[0].ok is False
    assert "模拟通道故障" in (result.observations[0].error or "")


def test_trace_会落盘(settings: Settings, data_root: Path) -> None:
    script = [
        PlanDecision(calls=[ToolCall(name="calculate", arguments={"expression": "2+2"})]),
        PlanDecision(final_text="好了"),
    ]
    agent = make_agent(script, settings)
    result = agent.chat("算一下 2+2")

    assert result.trace["steps"]
    target = data_root / "state" / "last_trace.json"
    assert target.is_file()
    assert "calculate" in target.read_text(encoding="utf-8")


def test_界面回调被调用(settings: Settings, data_root: Path) -> None:
    events: list[str] = []

    class Recorder:
        def on_thought(self, text, step):
            events.append("thought")

        def on_action(self, call, step):
            events.append("action")

        def on_observation(self, call, result, step):
            events.append("observation")

        def on_final(self, text):
            events.append("final")

    script = [
        PlanDecision(calls=[ToolCall(name="calculate", arguments={"expression": "2*3"})]),
        PlanDecision(final_text="好了"),
    ]
    agent = Agent(
        planner=FakePlanner(script),
        channel=ToolChannel("builtin"),
        memory=Memory(),
        settings=settings,
        observer=Recorder(),
    )
    agent.chat("算一下 2*3")
    assert events[:4] == ["thought", "action", "observation", "thought"]
    assert "final" in events


# ---------------------------------------------------------------- $ref 解析
def test_解析跨步引用() -> None:
    observations = [ToolResult.success("x", data={"days": [{"temp": 20}, {"temp": 22}]})]
    assert resolve_refs("$ref:steps[0].data.days", observations) == [{"temp": 20}, {"temp": 22}]
    assert resolve_refs("$ref:steps[-1].data.days", observations) == [{"temp": 20}, {"temp": 22}]


def test_解析失败的引用返回_None() -> None:
    observations = [ToolResult.success("x", data={})]
    assert resolve_refs("$ref:steps[5].data.x", observations) is None
    assert resolve_refs("$ref:steps[0].data.missing", observations) is None
    assert resolve_refs("$ref:steps[0].data.x", []) is None


def test_非引用值原样保留() -> None:
    assert resolve_refs({"a": 1, "b": ["x"]}, []) == {"a": 1, "b": ["x"]}
