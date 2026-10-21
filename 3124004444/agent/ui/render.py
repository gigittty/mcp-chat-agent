"""终端渲染：把「思考过程」画得看得懂。

设计目标：答辩时投影到大屏幕上，也能一眼看出「它在想什么、做了什么、
拿到了什么」。所以分三层信息：

    思考  │ 灰色，一行
    行动  │ 青色，显示 工具名(参数)
    观察  │ 绿色/红色，只显示结论 + 产物路径

图片会用 ASCII 缩略图直接打在终端里——不用切窗口就能看到「真的出图了」。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_RAMP = " .:-=+*#%@"


def enable_ansi() -> bool:
    """Windows 10+ 的控制台需要显式打开 VT 转义支持。"""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001
        return False


class Style:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    GREY = "\033[90m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"


_ENABLED = False


def init(enabled: bool = True) -> None:
    global _ENABLED
    _ENABLED = enabled and enable_ansi()
    if not _ENABLED:
        for name in dir(Style):
            if name.isupper():
                setattr(Style, name, "")


def paint(text: str, color: str) -> str:
    if not _ENABLED or not color:
        return text
    return f"{color}{text}{Style.RESET}"


def write(text: str = "") -> None:
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        # 控制台编码不支持某些字符时降级输出，不要让程序崩掉
        sys.stdout.write(text.encode("utf-8", "replace").decode("utf-8", "replace") + "\n")
        sys.stdout.flush()


def _display_width(text: str) -> int:
    """终端显示宽度：中日韩字符占两列，其余占一列。

    名字从中文换成英文后，原来的固定边框就对不齐了，所以改成按宽度算。
    """
    import unicodedata

    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def banner(mode_label: str, channel_label: str, tool_count: int) -> None:
    title = "Milk Dragon · 会说也会做的聊天智能体 v0.1"
    padding = max(2, (48 - _display_width(title)) // 2)
    inner = " " * padding + title
    inner += " " * max(0, 48 - _display_width(inner))
    write(paint("╭" + "─" * 48 + "╮", Style.MAGENTA))
    write(paint("│", Style.MAGENTA) + paint(inner, Style.BOLD) + paint("│", Style.MAGENTA))
    write(paint("╰" + "─" * 48 + "╯", Style.MAGENTA))
    write(paint(f"  模式：{mode_label}", Style.DIM))
    write(paint(f"  通道：{channel_label}　工具：{tool_count} 个", Style.DIM))
    write(paint("  输入 /help 看用法，/tools 看工具，/exit 退出", Style.DIM))
    write()


def user_echo(text: str) -> None:
    write(paint("你  ❯ ", Style.BOLD) + text)


def thought(text: str, step: int) -> None:
    if not text:
        return
    write(paint(f"     · 想：{text}", Style.GREY))


def action(name: str, arguments: dict) -> None:
    args = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    write(paint(f"     ▶ 做：{name}({args})", Style.CYAN))


def observation(result, via: str = "") -> None:
    tag = f"  ·{via}" if via else ""
    if result.ok:
        first_line = (result.text or "").splitlines()[0] if result.text else "完成"
        write(paint(f"     ✓ 得：{first_line}{tag}", Style.GREEN))
        if result.text and len(result.text.splitlines()) > 1:
            for line in result.text.splitlines()[1:5]:
                write(paint(f"           {line}", Style.DIM))
        for artifact in result.artifacts:
            write(paint(f"           📎 {artifact.path}", Style.YELLOW))
    else:
        write(paint(f"     ✗ 错：{result.error}{tag}", Style.RED))
        if result.hint:
            write(paint(f"           提示：{result.hint}", Style.DIM))


def reply(text: str) -> None:
    write()
    lines = text.splitlines() or [""]
    for index, line in enumerate(lines):
        prefix = paint("Milk Dragon ❯ ", Style.MAGENTA) if index == 0 else " " * 8
        write(prefix + line)
    write()


def image_preview(path: str, width: int = 46) -> str:
    """把图片转成 ASCII 缩略图。Pillow 不可用时返回空串。

    亮度方向会自适应：浅底图片（比如白底图表）用反向梯度，
    否则整块都会被画成最密的字符，看不出内容。
    """
    try:
        from PIL import Image
    except ImportError:
        return ""

    try:
        with Image.open(path) as image:
            image = image.convert("L")
            aspect = image.height / max(image.width, 1)
            # 终端字符大约是 1:2 的宽高比，高度要乘 0.5 才不会拉长
            height = max(4, int(width * aspect * 0.5))
            resized = image.resize((width, height))
            pixels = list(resized.getdata())
    except Exception:  # noqa: BLE001
        return ""

    ramp = _RAMP
    if pixels and sum(pixels) / len(pixels) > 127:
        ramp = _RAMP[::-1]      # 浅底 → 反相，让线条变成深色字符

    lines = []
    for row in range(height):
        chunk = pixels[row * width:(row + 1) * width]
        lines.append("".join(ramp[min(len(ramp) - 1, value * len(ramp) // 256)] for value in chunk))
    return "\n".join(lines)


def artifacts(result) -> None:
    for artifact in result.artifacts:
        if artifact.kind == "image" and Path(artifact.path).is_file():
            preview = image_preview(artifact.path)
            if preview:
                write(paint(preview, Style.DIM))


def help_text() -> str:
    return "\n".join(
        [
            "  直接说人话就行，比如：",
            "    北京今天天气怎么样",
            "    帮我查上海未来三天天气，然后画成柱状图",
            "    算一下 (12+8)*3/5 保留两位小数",
            "    画个梗图：周五了，代码还没写完",
            "    记一下：周四交作业",
            "    火锅还是烧烤，帮我选",
            "",
            "  命令：",
            "    /help    看这份帮助",
            "    /tools   列出当前可用工具（走 MCP 时是动态发现的）",
            "    /trace   查看上一轮的执行轨迹",
            "    /clear   清空短期记忆",
            "    /exit    退出",
        ]
    )


def tools_table(specs, active_mode: str) -> str:
    lines = [f"  当前通道：{active_mode}，共 {len(specs)} 个工具"]
    for spec in specs:
        lines.append(f"    {paint(spec.name.ljust(16), Style.CYAN)} {spec.title}")
    return "\n".join(lines)
