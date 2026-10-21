"""会话记忆。

分三层，各管各的：

- **短期**：最近若干轮对话，供规划器参考（只留少量工具原文，避免上下文膨胀）
- **待补槽位**：上一轮反问之后，用户还没回答的问题（多轮对话的粘合剂）
- **长期偏好**：落盘到 ``data/state/memory.json``，跨进程保留
  （「我住在杭州」「以后默认用折线图」）
"""

from __future__ import annotations

import json
import os
import re
from collections import deque
from pathlib import Path
from typing import Any

from agent.contracts import Message, ToolCall, ToolResult
from agent.planner.lexicon import PREF_PATTERNS
from agent.sandbox import state_root

MAX_TURNS = 12
MAX_TOOL_TEXT = 400


class Memory:
    def __init__(self, root: Path | None = None, max_turns: int = MAX_TURNS) -> None:
        self._root = root
        self.short: deque[Message] = deque(maxlen=max_turns)
        self.pending: dict[str, Any] | None = None
        self.prefs: dict[str, Any] = {}
        self.stats: dict[str, int] = {"turns": 0, "tool_calls": 0}
        self.previous: ToolResult | None = None
        self.load()

    # ------------------------------------------------------------------ 路径
    def _file(self) -> Path:
        root = self._root or state_root()
        root.mkdir(parents=True, exist_ok=True)
        return root / "memory.json"

    # ------------------------------------------------------------------ 读写
    def load(self) -> None:
        path = self._file()
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(payload, dict):
            return
        self.prefs = dict(payload.get("prefs") or {})
        stats = payload.get("stats") or {}
        if isinstance(stats, dict):
            self.stats.update({key: int(value) for key, value in stats.items() if isinstance(value, (int, float))})

    def save(self) -> None:
        path = self._file()
        payload = {"version": 1, "prefs": self.prefs, "stats": self.stats}
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    # ------------------------------------------------------------------ 短期
    def add_user(self, text: str) -> None:
        self.short.append(Message(role="user", content=text))
        self.stats["turns"] = self.stats.get("turns", 0) + 1

    def add_assistant(self, text: str) -> None:
        self.short.append(Message(role="assistant", content=text))

    def add_tool(self, call: ToolCall, result: ToolResult) -> None:
        text = result.text if len(result.text) <= MAX_TOOL_TEXT else result.text[:MAX_TOOL_TEXT] + "…"
        self.short.append(
            Message(role="tool", content=f"{call.name}: {text}", tool_calls=[call], tool_results=[result])
        )
        self.stats["tool_calls"] = self.stats.get("tool_calls", 0) + 1
        if result.ok:
            self.previous = result

    def recent(self, limit: int = 6) -> list[Message]:
        return list(self.short)[-limit:]

    # ------------------------------------------------------------------ 偏好
    def set_pref(self, key: str, value: Any) -> None:
        self.prefs[key] = value
        self.save()

    def learn_from(self, text: str) -> list[str]:
        """从用户这句话里抽取长期偏好。返回学到了什么（用于提示）。"""
        learned: list[str] = []
        for pattern, key, group in PREF_PATTERNS:
            match = re.search(pattern, text)
            if not match:
                continue
            value = match.group(group).strip()
            if not value:
                continue
            if key == "chart_kind":
                from agent.planner.lexicon import CHART_KINDS

                value = CHART_KINDS.get(value, value)
            if self.prefs.get(key) != value:
                self.prefs[key] = value
                learned.append(f"{key}={value}")
        if learned:
            self.save()
        return learned

    # ------------------------------------------------------------------ 其他
    def clear_pending(self) -> None:
        self.pending = None

    def reset(self) -> None:
        """清空短期记忆与待补槽位，保留长期偏好。"""
        self.short.clear()
        self.pending = None
        self.previous = None
