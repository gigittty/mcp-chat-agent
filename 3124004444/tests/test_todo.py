"""待办清单的持久化与状态流转测试。

重点验证：数据真的落盘了、写入是原子的、重新加载后能读回来。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.sandbox import state_root
from agent.tools import todo


def _store() -> Path:
    return state_root() / "todo.json"


def test_空清单(data_root: Path) -> None:
    result = todo.run({"action": "list"})
    assert result.ok
    assert result.data["count"] == 0
    assert "空" in result.text


def test_添加与查询(data_root: Path) -> None:
    added = todo.run({"action": "add", "item": "周四交作业", "due": "2026-10-23"})
    assert added.ok
    assert added.data["item"]["id"] == 1

    listed = todo.run({"action": "list"})
    assert listed.data["count"] == 1
    assert "周四交作业" in listed.text
    assert "2026-10-23" in listed.text


def test_数据真的落盘(data_root: Path) -> None:
    todo.run({"action": "add", "item": "写周报"})
    assert _store().is_file()
    payload = json.loads(_store().read_text(encoding="utf-8"))
    assert payload["items"][0]["text"] == "写周报"
    # 不应残留临时文件
    assert not list(state_root().glob("*.tmp"))


def test_完成与删除(data_root: Path) -> None:
    todo.run({"action": "add", "item": "A"})
    todo.run({"action": "add", "item": "B"})

    done = todo.run({"action": "done", "id": 1})
    assert done.ok
    assert done.data["pending"] == 1
    assert "✅" in todo.run({"action": "list"}).text

    removed = todo.run({"action": "remove", "id": 2})
    assert removed.ok
    assert todo.run({"action": "list"}).data["count"] == 1


def test_编号自增不回退(data_root: Path) -> None:
    todo.run({"action": "add", "item": "第一条"})
    todo.run({"action": "add", "item": "第二条"})
    todo.run({"action": "remove", "id": 2})
    third = todo.run({"action": "add", "item": "第三条"})
    assert third.data["item"]["id"] == 3


def test_清空已完成(data_root: Path) -> None:
    todo.run({"action": "add", "item": "已完成的事"})
    todo.run({"action": "add", "item": "未完成的事"})
    todo.run({"action": "done", "id": 1})
    result = todo.run({"action": "clear"})
    assert result.ok
    assert result.data["count"] == 1
    assert "未完成的事" in todo.run({"action": "list"}).text


def test_拒绝非法日期(data_root: Path) -> None:
    result = todo.run({"action": "add", "item": "带错误日期", "due": "明天"})
    assert result.ok is False
    assert result.hint


def test_找不到编号给出提示(data_root: Path) -> None:
    todo.run({"action": "add", "item": "只有这一条"})
    result = todo.run({"action": "done", "id": 99})
    assert result.ok is False
    assert "#1" in (result.hint or "")


def test_未知操作(data_root: Path) -> None:
    result = todo.run({"action": "explode"})
    assert result.ok is False
    assert result.hint


def test_文件损坏时自动恢复(data_root: Path) -> None:
    _store().write_text("{ 这不是合法 JSON", encoding="utf-8")
    result = todo.run({"action": "list"})
    assert result.ok
    assert result.data["count"] == 0
