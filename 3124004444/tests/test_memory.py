"""记忆的测试：短期滑窗、待补槽位、长期偏好落盘。"""

from __future__ import annotations

import json
from pathlib import Path

from agent.contracts import ToolCall, ToolResult
from agent.memory import Memory


def test_短期记忆是滑窗(data_root: Path) -> None:
    memory = Memory(max_turns=4)
    for index in range(10):
        memory.add_user(f"第{index}句")
    assert len(memory.short) == 4
    assert memory.short[-1].content == "第9句"


def test_记录工具调用会更新计数器与上一步结果(data_root: Path) -> None:
    memory = Memory()
    call = ToolCall(name="calculate", arguments={"expression": "1+1"})
    memory.add_tool(call, ToolResult.success("等于 2", data={"value": 2}))
    assert memory.stats["tool_calls"] == 1
    assert memory.previous is not None
    assert memory.previous.data["value"] == 2


def test_失败结果不会成为上一步(data_root: Path) -> None:
    memory = Memory()
    call = ToolCall(name="calculate", arguments={})
    memory.add_tool(call, ToolResult.failure("算不了"))
    assert memory.previous is None


def test_工具文本过长会被截断(data_root: Path) -> None:
    memory = Memory()
    call = ToolCall(name="files", arguments={})
    memory.add_tool(call, ToolResult.success("x" * 5000))
    assert len(memory.short[-1].content) < 600


def test_学习长期偏好(data_root: Path) -> None:
    memory = Memory()
    learned = memory.learn_from("我住在杭州")
    assert "city=杭州" in learned
    assert memory.prefs["city"] == "杭州"


def test_偏好会落盘并重新加载(data_root: Path) -> None:
    memory = Memory()
    memory.learn_from("我住在成都")
    memory.set_pref("chart_kind", "line")

    reloaded = Memory()
    assert reloaded.prefs["city"] == "成都"
    assert reloaded.prefs["chart_kind"] == "line"


def test_偏好文件内容合法(data_root: Path) -> None:
    memory = Memory()
    memory.learn_from("默认用折线图")
    payload = json.loads((data_root / "state" / "memory.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["prefs"]["chart_kind"] == "line"


def test_重置会清短期但保留偏好(data_root: Path) -> None:
    memory = Memory()
    memory.add_user("你好")
    memory.set_pref("city", "北京")
    memory.pending = {"name": "x"}

    memory.reset()
    assert len(memory.short) == 0
    assert memory.pending is None
    assert memory.prefs["city"] == "北京"


def test_记忆文件损坏时不影响启动(data_root: Path) -> None:
    target = data_root / "state" / "memory.json"
    target.write_text("{ 坏掉的 JSON", encoding="utf-8")
    memory = Memory()
    assert memory.prefs == {}


def test_待补槽位可以存取(data_root: Path) -> None:
    memory = Memory()
    memory.pending = {"name": "get_weather", "args": {}, "missing": ["city"]}
    assert memory.pending["missing"] == ["city"]
    memory.clear_pending()
    assert memory.pending is None
