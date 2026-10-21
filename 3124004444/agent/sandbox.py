"""文件路径沙箱。

``files`` 工具只能访问 ``data/files/`` 下的文件，任何越界尝试都抛
:class:`SandboxViolation`。这里不是「尽量拦」，而是**白名单式**校验：

1. 拒绝绝对路径、盘符、UNC、``..``
2. 逐段校验文件名（字符集、长度、Windows 保留名、结尾空格与点）
3. 落盘前再 resolve 一次，确认仍在沙箱根目录内（防符号链接绕过）

第 3 步是兜底：即使前面的规则被绕过，最终路径检查仍然拦得住。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from agent import data_dir
from agent.errors import SandboxViolation

MAX_FILENAME_LEN = 64
MAX_FILE_SIZE = 256 * 1024          # 单文件 256KB
ALLOWED_SUFFIXES = {".txt", ".md", ".json", ".csv", ".log", ".yml", ".yaml"}

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_FORBIDDEN_CHARS = set('<>:"|?*\\/\0')


def files_root() -> Path:
    """沙箱根目录：<data>/files。"""
    root = data_dir() / "files"
    return root


def out_root() -> Path:
    """生成图片的输出目录：<data>/out。"""
    return data_dir() / "out"


def state_root() -> Path:
    """持久化状态目录：<data>/state。"""
    return data_dir() / "state"


def ensure_dirs() -> None:
    for path in (files_root(), out_root(), state_root()):
        path.mkdir(parents=True, exist_ok=True)


def _validate_component(part: str) -> str:
    if not part:
        raise SandboxViolation("路径里有空的文件名片段")
    if part in (".", ".."):
        raise SandboxViolation("路径里不允许出现 . 或 ..")
    if len(part) > MAX_FILENAME_LEN:
        raise SandboxViolation(f"文件名太长（最多 {MAX_FILENAME_LEN} 字符）")
    if part != part.strip():
        raise SandboxViolation("文件名不能以空格开头或结尾")
    if part.endswith("."):
        raise SandboxViolation("文件名不能以点结尾")
    bad = _FORBIDDEN_CHARS & set(part)
    if bad:
        raise SandboxViolation(f"文件名包含非法字符：{''.join(sorted(bad))}")
    stem = part.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        raise SandboxViolation(f"{stem} 是 Windows 保留设备名，不能作为文件名")
    if any(ord(ch) < 32 for ch in part):
        raise SandboxViolation("文件名包含控制字符")
    return part


def resolve_inside(raw: str, *, root: Path | None = None, create_parent: bool = True) -> Path:
    """把用户给的相对路径解析为沙箱内的绝对路径。

    Raises:
        SandboxViolation: 任何越界或非法路径。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise SandboxViolation("文件路径不能为空")

    text = raw.strip().replace("\\", "/")
    if text.startswith("/"):
        raise SandboxViolation("不允许使用绝对路径")
    if PureWindowsPath(text).drive:
        raise SandboxViolation("不允许使用带盘符的路径")
    if text.startswith("//"):
        raise SandboxViolation("不允许使用 UNC 路径")

    base = (root or files_root())
    base.mkdir(parents=True, exist_ok=True)
    base_resolved = base.resolve()

    parts = [_validate_component(p) for p in PurePosixPath(text).parts]
    if not parts:
        raise SandboxViolation("文件路径不能为空")

    candidate = base_resolved.joinpath(*parts)
    parent = candidate.parent
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True)

    # 兜底：resolve 之后必须仍在沙箱内（防符号链接 / 大小写差异绕过）
    resolved = candidate.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise SandboxViolation("路径越界：只能访问数据目录内的文件") from exc
    return resolved


def check_suffix(path: Path) -> None:
    """扩展名白名单。"""
    suffix = path.suffix.lower()
    if suffix and suffix not in ALLOWED_SUFFIXES:
        allowed = "、".join(sorted(ALLOWED_SUFFIXES))
        raise SandboxViolation(f"不支持的文件类型 {suffix}，仅允许：{allowed}")


def check_size(size: int) -> None:
    if size > MAX_FILE_SIZE:
        raise SandboxViolation(f"内容太大（{size} 字节），上限 {MAX_FILE_SIZE} 字节约 256KB")
