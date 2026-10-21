"""路径沙箱的安全性测试。

原则：宁可多拦，不可漏放。每个用例都对应一种真实的绕过手法。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.errors import SandboxViolation
from agent.sandbox import (
    check_size,
    check_suffix,
    files_root,
    resolve_inside,
)


def test_正常相对路径(data_root: Path) -> None:
    path = resolve_inside("note.md")
    assert path.parent == files_root().resolve()
    assert path.name == "note.md"


def test_允许子目录(data_root: Path) -> None:
    path = resolve_inside("sub/dir/a.txt")
    assert path.name == "a.txt"
    assert path.is_relative_to(files_root().resolve())


@pytest.mark.parametrize(
    "bad",
    [
        "../secret.txt",
        "../../etc/passwd",
        "sub/../../escape.txt",
        "./../x.txt",
        "..",
    ],
)
def test_拒绝目录穿越(data_root: Path, bad: str) -> None:
    with pytest.raises(SandboxViolation):
        resolve_inside(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "C:/Windows/System32/drivers/etc/hosts",
        "C:\\Windows\\win.ini",
        "D:/data.txt",
        "/etc/passwd",
        "\\\\server\\share\\a.txt",
        "//server/share/a.txt",
    ],
)
def test_拒绝绝对路径与UNC(data_root: Path, bad: str) -> None:
    with pytest.raises(SandboxViolation):
        resolve_inside(bad)


@pytest.mark.parametrize("bad", ["CON", "con.txt", "NUL", "COM1", "LPT9", "aux.md"])
def test_拒绝_windows_保留设备名(data_root: Path, bad: str) -> None:
    with pytest.raises(SandboxViolation):
        resolve_inside(bad)


@pytest.mark.parametrize("bad", ["a<b.txt", "a?b.txt", 'a"b.txt', "a|b.txt", "a*b.txt", "a:b.txt"])
def test_拒绝非法字符(data_root: Path, bad: str) -> None:
    with pytest.raises(SandboxViolation):
        resolve_inside(bad)


@pytest.mark.parametrize("bad", ["sub/ leading.txt", "sub/dotend./a.txt", "dotend."])
def test_拒绝组件首尾空格与结尾点(data_root: Path, bad: str) -> None:
    with pytest.raises(SandboxViolation):
        resolve_inside(bad)


def test_外层空白会被忽略(data_root: Path) -> None:
    """整条路径首尾的空白是用户输入习惯，宽容处理；但文件名内部的空白仍然校验。"""
    assert resolve_inside("  note.md  ").name == "note.md"


def test_拒绝超长文件名(data_root: Path) -> None:
    with pytest.raises(SandboxViolation):
        resolve_inside("a" * 80 + ".txt")


def test_拒绝空路径(data_root: Path) -> None:
    for bad in ("", "   "):
        with pytest.raises(SandboxViolation):
            resolve_inside(bad)


def test_反斜杠被当成路径分隔符而不是绕过手段(data_root: Path) -> None:
    # 统一换成 / 之后再逐段校验，所以 ..\..\ 一样被拦
    with pytest.raises(SandboxViolation):
        resolve_inside("..\\..\\secret.txt")


def test_允许中文文件名(data_root: Path) -> None:
    path = resolve_inside("天气记录.md")
    assert path.name == "天气记录.md"


def test_扩展名白名单(data_root: Path) -> None:
    check_suffix(Path("a.md"))
    check_suffix(Path("a.json"))
    for bad in ("a.exe", "a.bat", "a.ps1", "a.sh", "a.dll"):
        with pytest.raises(SandboxViolation):
            check_suffix(Path(bad))


def test_文件大小上限() -> None:
    check_size(1024)
    with pytest.raises(SandboxViolation):
        check_size(1024 * 1024)


def test_符号链接不能逃逸(data_root: Path) -> None:
    """在沙箱内建一个指向外部目录的符号链接，写入时必须被拦。"""
    outside = data_root.parent / "outside"
    outside.mkdir(exist_ok=True)
    link = files_root() / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接（需要管理员或开发者模式）")
    if not link.is_symlink():
        # Windows 上无权限时 symlink_to 可能既不报错也没真正建立链接
        pytest.skip("符号链接未被真正创建，跳过该用例")

    with pytest.raises(SandboxViolation):
        resolve_inside("link/evil.txt")
