"""环境自检脚本。

用途：换一台机器后先跑这个，一次看清「缺什么、能不能跑」。
    python scripts/check_env.py

设计要点：所有探测都容错，缺包不会崩，只会在表里打叉。
"""

from __future__ import annotations

import importlib
import os
import platform
import socket
import sys
import time
from pathlib import Path

# 允许直接以脚本方式运行（python scripts/check_env.py）
CODE_ROOT = Path(__file__).resolve().parent.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

OK = "[ OK ]"
NO = "[FAIL]"
WARN = "[WARN]"


def line(title: str) -> None:
    print(f"\n=== {title} ===")


def _package_version(module_name: str, module: object) -> str:
    """优先从包元数据取版本（mcp 没有 __version__ 属性）。"""
    try:
        from importlib.metadata import version as dist_version

        return dist_version(module_name.replace("_", "-"))
    except Exception:  # noqa: BLE001
        return str(getattr(module, "__version__", "?"))


def check_python() -> None:
    line("Python")
    print(f"  版本      : {platform.python_version()} ({platform.architecture()[0]})")
    print(f"  解释器    : {sys.executable}")
    print(f"  平台      : {platform.system()} {platform.release()}")
    if sys.version_info < (3, 10):
        print(f"  {NO} 需要 Python >= 3.10")
    else:
        print(f"  {OK} 版本满足要求")


def check_packages() -> None:
    line("依赖包")
    # (导入名, 展示名, 是否必需, 说明)
    packages = [
        ("mcp", "mcp (MCP SDK)", True, "MCP 协议支持，必须是 2.x"),
        ("PIL", "Pillow", True, "生成梗图/海报"),
        ("matplotlib", "matplotlib", True, "生成图表"),
        ("pytest", "pytest", False, "运行测试"),
        ("streamlit", "streamlit", False, "Web 界面"),
    ]
    for module_name, display, required, note in packages:
        try:
            module = importlib.import_module(module_name)
            version = _package_version(module_name, module)
            tag = OK
            extra = ""
            if module_name == "mcp":
                major = str(version).split(".")[0]
                if major == "1":
                    tag = WARN
                    extra = " ← 检测到 1.x，本项目需要 2.x：pip install -U 'mcp>=2.3'"
            print(f"  {tag} {display:<20} {version}{extra}")
        except Exception as exc:  # noqa: BLE001 - 自检脚本要吞掉一切异常
            tag = NO if required else WARN
            suffix = "（必需）" if required else "（可选，缺失时该功能不可用）"
            print(f"  {tag} {display:<20} 未安装{suffix} {type(exc).__name__}")
            print(f"         用途：{note}")


def _probe_font(path: Path) -> bool:
    """真正渲染一个汉字，看是否画出了可见像素（能识别「豆腐块」）。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return False
    try:
        font = ImageFont.truetype(str(path), 32)
        img = Image.new("L", (48, 48), 0)
        ImageDraw.Draw(img).text((4, 4), "测", font=font, fill=255)
        return img.getbbox() is not None
    except Exception:  # noqa: BLE001
        return False


def check_fonts() -> None:
    line("中文字体（生成中文图片必备）")
    windir = os.environ.get("WINDIR", "C:/Windows")
    candidates = [
        Path(windir) / "Fonts" / "msyh.ttc",
        Path(windir) / "Fonts" / "msyhbd.ttc",
        Path(windir) / "Fonts" / "simhei.ttf",
        Path(windir) / "Fonts" / "simsun.ttc",
        Path(windir) / "Fonts" / "Deng.ttf",
    ]
    hit = 0
    for path in candidates:
        if not path.is_file():
            print(f"  {WARN} 不存在  {path.name}")
            continue
        if _probe_font(path):
            print(f"  {OK} 可用    {path}")
            hit += 1
        else:
            print(f"  {WARN} 渲染失败 {path.name}（可能是字体损坏或未安装 Pillow）")
    if hit == 0:
        print(f"  {NO} 一个可用中文字体都没有，生成的图片会显示方块")
        print("         解决：安装「微软雅黑」或「黑体」，或改用 ImageFont.load_default(size=)")


def _http_probe(url: str, timeout: float = 5.0) -> tuple[bool, float, str]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; mcp-chat-agent/env-check)"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(2048)
        return True, (time.perf_counter() - started) * 1000, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, (time.perf_counter() - started) * 1000, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, (time.perf_counter() - started) * 1000, type(exc).__name__


def check_network() -> None:
    line("网络（天气工具需要；全部失败也能跑，会走离线示例数据）")
    targets = [
        ("主源 wttr.in", "https://wttr.in/?format=j1"),
        ("备源 open-meteo", "https://geocoding-api.open-meteo.com/v1/search?name=beijing&count=1"),
    ]
    for name, url in targets:
        ok, elapsed, detail = _http_probe(url)
        tag = OK if ok else WARN
        print(f"  {tag} {name:<18} {detail:<12} {elapsed:6.0f} ms")
    try:
        socket.setdefaulttimeout(3)
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        print(f"  {OK} 基础连通性正常")
    except Exception:  # noqa: BLE001
        print(f"  {WARN} 基础连通性不通（离线环境，属正常）")


def check_config() -> None:
    line("配置")
    try:
        from agent.config import get_settings
    except Exception as exc:  # noqa: BLE001
        print(f"  {NO} 无法导入 agent.config：{exc}")
        return
    settings = get_settings()
    env_file = CODE_ROOT / ".env"
    print(f"  {OK if env_file.is_file() else WARN} .env 文件     {'已存在' if env_file.is_file() else '未创建（使用默认值，可复制 .env.example）'}")
    print(f"  {OK} 规划器模式   {settings.agent_mode} —— {settings.mode_label}")
    print(f"  {OK} 工具通道     {settings.mcp_channel}")
    print(f"  {OK} 最大步数     {settings.max_steps}")
    print(f"  {OK} 离线模式     {'开' if settings.offline else '关'}")


def check_tools() -> None:
    line("工具注册表")
    try:
        from agent.registry import get_registry
    except Exception as exc:  # noqa: BLE001
        print(f"  {WARN} 暂不可用（{type(exc).__name__}）：{exc}")
        return
    specs = get_registry().all()
    if not specs:
        print(f"  {WARN} 当前没有任何工具（后续提交会逐步加入）")
        return
    for spec in specs:
        print(f"  {OK} {spec.name:<18} {spec.title}")
    print(f"  共 {len(specs)} 个工具")


def check_mcp() -> None:
    line("MCP 协议（工具是否能通过标准协议暴露）")
    try:
        import mcp
    except Exception as exc:  # noqa: BLE001
        print(f"  {WARN} 未安装 mcp（{type(exc).__name__}），只能走内置通道")
        return

    from importlib.metadata import version as dist_version

    print(f"  {OK} SDK 版本   {dist_version('mcp')}")

    try:
        import asyncio

        from mcp_server.server import server

        async def handshake():
            async with mcp.Client(server) as client:
                listed = await client.list_tools()
                return listed, client.protocol_version

        listed, protocol = asyncio.run(handshake())
    except Exception as exc:  # noqa: BLE001
        print(f"  {NO} 握手失败：{type(exc).__name__}: {exc}")
        print("         排查：确认 mcp 是 2.x（pip install -U 'mcp>=2.3'）")
        return

    print(f"  {OK} 协议版本   {protocol}")
    print(f"  {OK} 握手成功   暴露 {len(listed.tools)} 个工具")
    names = ", ".join(tool.name for tool in listed.tools)
    print(f"         工具列表   {names}")


def main() -> int:
    print("=" * 62)
    print(" 团子龙聊天智能体 —— 环境自检")
    print("=" * 62)
    check_python()
    check_packages()
    check_fonts()
    check_network()
    check_config()
    check_tools()
    check_mcp()
    print("\n" + "=" * 62)
    print(" 自检结束。若 mcp 显示 1.x，请执行：pip install -U 'mcp>=2.3'")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
