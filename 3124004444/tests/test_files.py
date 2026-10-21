"""沙箱文件工具的测试。

除功能正确性外，重点验证「越界写不进去」——这是最容易出事的地方。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.sandbox import files_root
from agent.tools import files


def test_写入后能读回(data_root: Path) -> None:
    written = files.run({"action": "write", "path": "note.md", "content": "# 标题\n正文"})
    assert written.ok

    read_back = files.run({"action": "read", "path": "note.md"})
    assert read_back.ok
    assert "正文" in read_back.data["content"]
    assert read_back.data["bytes"] > 0


def test_追加不会覆盖(data_root: Path) -> None:
    files.run({"action": "write", "path": "log.txt", "content": "第一行\n"})
    files.run({"action": "append", "path": "log.txt", "content": "第二行\n"})
    content = files.run({"action": "read", "path": "log.txt"}).data["content"]
    assert "第一行" in content
    assert "第二行" in content


def test_支持子目录(data_root: Path) -> None:
    result = files.run({"action": "write", "path": "记录/todo.txt", "content": "x"})
    assert result.ok
    assert result.data["relative"] == "记录/todo.txt"


def test_列出文件(data_root: Path) -> None:
    files.run({"action": "write", "path": "a.txt", "content": "1"})
    files.run({"action": "write", "path": "b.md", "content": "2"})
    listed = files.run({"action": "list"})
    assert listed.ok
    assert listed.data["count"] == 2
    names = {item["name"] for item in listed.data["entries"]}
    assert names == {"a.txt", "b.md"}


def test_空目录提示(data_root: Path) -> None:
    result = files.run({"action": "list"})
    assert result.ok
    assert result.data["count"] == 0


def test_删除(data_root: Path) -> None:
    files.run({"action": "write", "path": "temp.txt", "content": "x"})
    assert files.run({"action": "delete", "path": "temp.txt"}).ok
    assert files.run({"action": "read", "path": "temp.txt"}).ok is False


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.txt",
        "../../Windows/evil.txt",
        "C:/Windows/evil.txt",
        "/etc/passwd",
        "\\\\server\\share\\x.txt",
        "CON",
        "a.exe",
    ],
)
def test_越界与非法文件被拦截(data_root: Path, bad_path: str) -> None:
    result = files.run({"action": "write", "path": bad_path, "content": "hacked"})
    assert result.ok is False
    assert result.hint
    # 确认真的没写出去
    assert not (data_root.parent / "escape.txt").exists()


def test_内容过大被拒绝(data_root: Path) -> None:
    result = files.run({"action": "write", "path": "big.txt", "content": "x" * (300 * 1024)})
    assert result.ok is False


def test_缺少参数(data_root: Path) -> None:
    assert files.run({"action": "write", "content": "x"}).ok is False
    assert files.run({"action": "write", "path": "a.txt"}).ok is False
    assert files.run({"action": "read", "path": "不存在.txt"}).ok is False
    assert files.run({"action": "unknown", "path": "a.txt"}).ok is False


def test_产出文件在沙箱内(data_root: Path) -> None:
    result = files.run({"action": "write", "path": "inside.txt", "content": "x"})
    assert result.ok
    assert Path(result.data["path"]).is_relative_to(files_root().resolve())
