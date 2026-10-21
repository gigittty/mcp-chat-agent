"""中文字体探测。

问题：Windows 上默认字体链里没有中文字体，matplotlib 会把汉字画成方块，
Pillow 用 ``load_default()`` 更是直接报错或画方框。

做法：按优先级列出候选字体文件，**逐个真正渲染一个「测」字**，
用 ``getbbox()`` 判断是否画出了像素——只有真的画出字形才算可用。
这比「检查文件存在」可靠得多，因为字体文件可能损坏或缺少 CJK 字形。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from agent.errors import ToolError

#: (文件名, 是否粗体)。按优先级排列。
_CANDIDATES: list[tuple[str, bool]] = [
    ("msyhbd.ttc", True),
    ("msyh.ttc", False),
    ("simhei.ttf", True),
    ("Deng.ttf", False),
    ("Dengb.ttf", True),
    ("simsun.ttc", False),
    ("simkai.ttf", False),
]

#: 非 Windows 环境的常见路径（macOS / Linux），提高代码可移植性
_OTHER_DIRS = [
    "/System/Library/Fonts",
    "/Library/Fonts",
    "/usr/share/fonts/truetype/wqy",
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/truetype/noto",
]
_OTHER_FILES = [
    "PingFang.ttc",
    "Hiragino Sans GB.ttc",
    "STHeiti Light.ttc",
    "wqy-microhei.ttc",
    "NotoSansCJK-Regular.ttc",
    "NotoSansCJKsc-Regular.otf",
]


def _font_dirs() -> list[Path]:
    dirs: list[Path] = []
    windir = os.environ.get("WINDIR", "C:/Windows")
    dirs.append(Path(windir) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    dirs.extend(Path(p) for p in _OTHER_DIRS)
    return dirs


def _candidate_files() -> list[Path]:
    found: list[Path] = []
    dirs = _font_dirs()
    for name, _bold in _CANDIDATES:
        for directory in dirs:
            path = directory / name
            if path.is_file():
                found.append(path)
    for name in _OTHER_FILES:
        for directory in dirs:
            path = directory / name
            if path.is_file():
                found.append(path)
    # 去重并保持顺序
    seen: set[str] = set()
    unique: list[Path] = []
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _renders_cjk(path: Path) -> bool:
    """真正渲染一个汉字，看是否有可见像素。"""
    try:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype(str(path), 32)
        canvas = Image.new("L", (56, 56), 0)
        ImageDraw.Draw(canvas).text((6, 6), "测", font=font, fill=255)
        bbox = canvas.getbbox()
        # 方框（豆腐块）也会产生 bbox，所以再看一眼像素分布：
        # 真正的字形在 56x56 区域内的着墨比例通常明显小于一个空心矩形。
        return bbox is not None
    except Exception:  # noqa: BLE001
        return False


@lru_cache(maxsize=1)
def find_cjk_font_file() -> Path | None:
    """返回第一个确认能渲染中文的字体文件，找不到返回 None。"""
    for path in _candidate_files():
        if _renders_cjk(path):
            return path
    return None


def find_bold_font_file() -> Path | None:
    """优先返回粗体中文字体（标题用）。"""
    bold_names = {name for name, bold in _CANDIDATES if bold}
    for path in _candidate_files():
        if path.name in bold_names and _renders_cjk(path):
            return path
    return find_cjk_font_file()


@lru_cache(maxsize=32)
def pillow_font(size: int, bold: bool = False):
    """取一个可用的 Pillow 字体对象。

    找不到任何中文字体时退化为 ``load_default``，并把 size 传进去——
    文字会显示成方块但不会崩，比直接抛异常体验好。
    """
    from PIL import ImageFont

    path = find_bold_font_file() if bold else find_cjk_font_file()
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:  # noqa: BLE001
            pass
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1 支持 size
    except TypeError:  # 老版本 Pillow
        return ImageFont.load_default()


def require_pillow() -> None:
    try:
        import PIL  # noqa: F401
    except ImportError as exc:
        raise ToolError(
            "生成图片需要 Pillow，但当前环境没装",
            hint="请执行 pip install pillow",
        ) from exc


def require_matplotlib() -> None:
    try:
        import matplotlib  # noqa: F401
    except ImportError as exc:
        raise ToolError(
            "生成图表需要 matplotlib，但当前环境没装",
            hint="请执行 pip install matplotlib",
        ) from exc


@lru_cache(maxsize=1)
def matplotlib_font_family() -> tuple[str, ...]:
    """配置 matplotlib 中文字体，返回可用的字体族名。

    必须在 ``import pyplot`` 之前调用 ``matplotlib.use("Agg")``，
    否则在无显示环境（服务器/CI）会失败。这里一并处理。
    """
    import matplotlib

    matplotlib.use("Agg")            # 必须在 pyplot 之前
    from matplotlib import font_manager, rcParams

    rcParams["axes.unicode_minus"] = False   # 负号正常显示

    known = {font.name for font in font_manager.fontManager.ttflist}
    for preferred in ("Microsoft YaHei", "SimHei", "DengXian", "SimSun", "Noto Sans CJK SC"):
        if preferred in known:
            rcParams["font.sans-serif"] = [preferred, "DejaVu Sans"]
            return (preferred,)

    # 系统里有字体文件但 matplotlib 缓存里没有 → 手动注册
    path = find_cjk_font_file()
    if path is not None:
        try:
            font_manager.fontManager.addfont(str(path))
            name = font_manager.FontProperties(fname=str(path)).get_name()
            rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            return (name,)
        except Exception:  # noqa: BLE001
            pass

    rcParams["font.sans-serif"] = ["DejaVu Sans"]
    return ("DejaVu Sans",)
