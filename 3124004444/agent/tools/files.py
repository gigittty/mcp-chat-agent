"""沙箱内的文本文件读写。

所有路径都经过 :mod:`agent.sandbox` 的白名单校验，只能落在 ``data/files/``
之下；扩展名有白名单，单文件有大小上限。

这是智能体「能动手」的关键一环——用户说「把刚才的结果存成文件」时，
它真的会在磁盘上写出一个文件。
"""

from __future__ import annotations

from pathlib import Path

from agent.contracts import Artifact, ToolResult, ToolSpec
from agent.errors import SandboxViolation
from agent.sandbox import (
    MAX_FILE_SIZE,
    check_size,
    check_suffix,
    files_root,
    resolve_inside,
)

PREVIEW_CHARS = 800

SPEC = ToolSpec(
    name="files",
    title="文件读写",
    description=(
        "在受保护的沙箱目录里读写文本文件。action=write 写入（覆盖）、read 读取、"
        "append 追加、list 列出已有文件、delete 删除。path 必须是相对路径，"
        "支持 .txt/.md/.json/.csv/.log/.yml 等文本类型。"
        "用户要求保存内容到文件、查看文件、列出文件时使用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "read", "append", "list", "delete"],
                "description": "要执行的操作",
            },
            "path": {
                "type": "string",
                "description": "相对路径，例如 weather.md 或 记录/todo.txt",
            },
            "content": {
                "type": "string",
                "description": "要写入的内容（write/append 时必填）",
            },
        },
        "required": ["action"],
    },
    tags=["files"],
    produces=["files.path"],
    examples=[{"action": "write", "path": "weather.md", "content": "# 天气记录"}],
)


def _list_entries(limit: int = 40) -> list[dict[str, object]]:
    root = files_root()
    root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        try:
            entries.append(
                {
                    "name": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                }
            )
        except OSError:
            continue
        if len(entries) >= limit:
            break
    return entries


def run(args: dict) -> ToolResult:
    action = str(args.get("action", "")).strip().lower()

    if action == "list":
        entries = _list_entries()
        if not entries:
            return ToolResult.success(
                "沙箱目录里还没有文件，要不要先写一个？",
                data={"entries": [], "count": 0},
            )
        lines = [f"{item['name']}  ({item['bytes']} 字节)" for item in entries]
        return ToolResult.success(
            f"沙箱里共 {len(entries)} 个文件：\n" + "\n".join(f"  {line}" for line in lines),
            data={"entries": entries, "count": len(entries)},
        )

    raw_path = str(args.get("path", "") or "").strip()
    if not raw_path:
        return ToolResult.failure("还没说要对哪个文件操作", hint="请给出文件名，例如 weather.md")

    try:
        target: Path = resolve_inside(raw_path)
        check_suffix(target)
    except SandboxViolation as exc:
        return ToolResult.failure(str(exc), hint="只能操作 data/files 目录下的文本文件，且不能使用 .. 或绝对路径")

    if action == "read":
        if not target.is_file():
            return ToolResult.failure(f"文件不存在：{raw_path}", hint="可以先 list 看看有哪些文件")
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.failure(f"读取失败：{exc}")
        preview = text[:PREVIEW_CHARS]
        suffix = "…" if len(text) > PREVIEW_CHARS else ""
        return ToolResult.success(
            f"{raw_path}（{len(text)} 字节）：\n{preview}{suffix}",
            data={"path": str(target), "bytes": target.stat().st_size, "content": text},
        )

    if action in ("write", "append"):
        content = args.get("content")
        if content is None:
            return ToolResult.failure("没有给出要写入的内容", hint="请提供 content 字段")
        content = str(content)
        payload = content.encode("utf-8")
        try:
            check_size(len(payload))
            if action == "append" and target.is_file():
                existing = target.stat().st_size
                check_size(existing + len(payload))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a" if action == "append" else "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except SandboxViolation as exc:
            return ToolResult.failure(str(exc))
        except OSError as exc:
            return ToolResult.failure(f"写入失败：{exc}")

        verb = "追加到" if action == "append" else "写入"
        relative = target.relative_to(files_root().resolve()).as_posix()
        return ToolResult.success(
            f"已{verb} {relative}（{len(payload)} 字节）",
            data={"path": str(target), "relative": relative, "bytes": len(payload), "action": action},
            artifacts=[Artifact(kind="file", path=str(target), mime="text/plain", caption=relative)],
        )

    if action == "delete":
        if not target.is_file():
            return ToolResult.failure(f"文件不存在：{raw_path}")
        try:
            target.unlink()
        except OSError as exc:
            return ToolResult.failure(f"删除失败：{exc}")
        return ToolResult.success(f"已删除 {raw_path}", data={"removed": raw_path})

    return ToolResult.failure(
        f"不认识的操作：{action or '（空）'}",
        hint="可用操作：write / read / append / list / delete",
    )


if __name__ == "__main__":
    print(run({"action": "write", "path": "demo.md", "content": "# 你好\n这是团子龙写的。"}).text)
    print(run({"action": "list"}).text)
