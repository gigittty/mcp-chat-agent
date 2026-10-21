"""待办清单：增、查、完成、删除。

持久化到 ``data/state/todo.json``，采用「先写临时文件再原子替换」，
避免进程在写一半时被杀导致 JSON 损坏。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.contracts import ToolResult, ToolSpec
from agent.sandbox import state_root

MAX_ITEMS = 200
MAX_TEXT_LEN = 200

_LOCK = threading.Lock()

SPEC = ToolSpec(
    name="todo",
    title="待办清单",
    description=(
        "管理待办事项。action=add 添加（需要 item，可选 due 截止日期）、list 查看全部、"
        "done 标记完成（需要 id）、remove 删除（需要 id）、clear 清空已完成。"
        "用户说「记一下」「帮我记着」「列一下待办」「这个做完了」时使用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "done", "remove", "clear"],
                "description": "要执行的操作",
            },
            "item": {
                "type": "string",
                "description": "待办内容（add 时必填）",
            },
            "due": {
                "type": "string",
                "description": "截止日期，形如 2026-10-25，可省略",
            },
            "id": {
                "type": "integer",
                "minimum": 1,
                "description": "待办编号（done/remove 时必填）",
            },
        },
        "required": ["action"],
    },
    tags=["state", "files"],
    produces=["todo.items"],
    examples=[{"action": "add", "item": "周四交作业", "due": "2026-10-23"}],
)


def _store_path() -> Path:
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / "todo.json"


def _empty() -> dict[str, Any]:
    return {"version": 1, "next_id": 1, "items": []}


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 文件损坏时不要让用户看到崩溃：重置并继续
        return _empty()
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        return _empty()
    raw.setdefault("version", 1)
    raw.setdefault("next_id", max([int(i.get("id", 0)) for i in raw["items"]] or [0]) + 1)
    return raw


def _save(path: Path, data: dict[str, Any]) -> None:
    """原子写：先写 .tmp，再 os.replace（同盘替换是原子的）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _format_items(items: list[dict[str, Any]], only_pending: bool = False) -> str:
    if not items:
        return "待办清单是空的，本龙可以躺平了～"
    lines: list[str] = []
    for item in items:
        if only_pending and item.get("done"):
            continue
        mark = "✅" if item.get("done") else "⬜"
        due = f"（截止 {item['due']}）" if item.get("due") else ""
        lines.append(f"{mark} #{item['id']} {item['text']}{due}")
    return "\n".join(lines) if lines else "没有未完成的待办，全部搞定啦！"


def _pending_count(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if not item.get("done"))


def run(args: dict) -> ToolResult:
    action = str(args.get("action", "")).strip().lower()
    path = _store_path()

    with _LOCK:
        data = _load(path)
        items: list[dict[str, Any]] = data["items"]

        if action == "add":
            text = str(args.get("item", "")).strip()
            if not text:
                return ToolResult.failure("要记什么内容呢？", hint="请给出待办内容，例如「周四交作业」")
            if len(text) > MAX_TEXT_LEN:
                return ToolResult.failure(f"待办内容太长（上限 {MAX_TEXT_LEN} 字）")
            if len(items) >= MAX_ITEMS:
                return ToolResult.failure(f"待办太多了（上限 {MAX_ITEMS} 条），先清理一些吧")

            due = str(args.get("due", "") or "").strip()
            if due:
                try:
                    datetime.strptime(due, "%Y-%m-%d")
                except ValueError:
                    return ToolResult.failure(
                        f"截止日期格式不对：{due}",
                        hint="请用 2026-10-25 这样的格式，或者干脆不写日期",
                    )

            entry = {
                "id": int(data["next_id"]),
                "text": text,
                "due": due,
                "done": False,
                "created": datetime.now().isoformat(timespec="seconds"),
            }
            items.append(entry)
            data["next_id"] = entry["id"] + 1
            _save(path, data)
            tail = f"，截止 {due}" if due else ""
            return ToolResult.success(
                f"记下啦：#{entry['id']} {text}{tail}（还有 {_pending_count(items)} 件事没做完）",
                data={"item": entry, "count": len(items), "pending": _pending_count(items)},
            )

        if action == "list":
            pending = _pending_count(items)
            text = _format_items(items)
            header = f"共 {len(items)} 条待办，{pending} 条未完成：\n" if items else ""
            return ToolResult.success(
                header + text,
                data={"items": items, "count": len(items), "pending": pending},
            )

        if action in ("done", "remove"):
            try:
                target_id = int(args.get("id", 0))
            except (TypeError, ValueError):
                return ToolResult.failure("请告诉我要操作哪一条待办", hint="用 # 后面的编号，例如「把 #2 标记完成」")
            match = next((item for item in items if int(item.get("id", 0)) == target_id), None)
            if match is None:
                ids = ", ".join(f"#{i['id']}" for i in items) or "（当前没有待办）"
                return ToolResult.failure(f"找不到 #{target_id}", hint=f"现有编号：{ids}")

            if action == "done":
                match["done"] = True
                match["done_at"] = datetime.now().isoformat(timespec="seconds")
                _save(path, data)
                return ToolResult.success(
                    f"完成啦！#{target_id} {match['text']}（还剩 {_pending_count(items)} 件）",
                    data={"item": match, "pending": _pending_count(items)},
                )

            items.remove(match)
            _save(path, data)
            return ToolResult.success(
                f"已删除 #{target_id} {match['text']}",
                data={"removed": match, "count": len(items)},
            )

        if action == "clear":
            before = len(items)
            data["items"] = [item for item in items if not item.get("done")]
            _save(path, data)
            removed = before - len(data["items"])
            return ToolResult.success(
                f"清掉了 {removed} 条已完成的待办，还剩 {len(data['items'])} 条",
                data={"removed": removed, "count": len(data["items"])},
            )

    return ToolResult.failure(
        f"不认识的操作：{action or '（空）'}",
        hint="可用操作：add / list / done / remove / clear",
    )
