"""MCP 端到端冒烟测试：真的起一个子进程，走 stdio 协议。

这是唯一能证明「不只是本地函数调用、而是真的通过了 MCP 协议」的测试。
没装 mcp 包时整体跳过（项目仍可用内置通道运行）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp", reason="未安装 mcp 包，跳过 MCP 协议测试")

CODE_ROOT = Path(__file__).resolve().parent.parent
HANDSHAKE_BUDGET = 30.0


def _params(extra_env: dict[str, str] | None = None):
    env = {
        **os.environ,
        # 子进程不继承父进程环境，以下必须显式给
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(CODE_ROOT),
        "AGENT_OFFLINE": "1",          # 让天气走离线分支，测试不依赖外网
    }
    if extra_env:
        env.update(extra_env)
    return mcp.StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "mcp_server"],
        cwd=str(CODE_ROOT),
        env=env,
    )


def _run(coro_factory):
    return asyncio.run(coro_factory())


def test_握手并列出工具() -> None:
    async def scenario():
        async with mcp.Client(_params()) as client:
            started = time.perf_counter()
            listed = await client.list_tools()
            elapsed = time.perf_counter() - started
            return listed, elapsed, client.server_info

    listed, elapsed, info = _run(scenario)
    names = {tool.name for tool in listed.tools}
    assert names == {
        "calculate", "fun_pick", "get_weather", "todo",
        "make_chart", "make_meme", "make_ascii_art", "files",
    }
    assert elapsed < HANDSHAKE_BUDGET
    assert info is not None and info.name == "tuanzilong-agent"


def test_通过协议调用工具() -> None:
    async def scenario():
        async with mcp.Client(_params()) as client:
            return await client.call_tool(
                "calculate", {"expression": "(12+8)*3/5", "precision": 2}
            )

    result = _run(scenario)
    assert result.is_error is False

    # 服务端统一返回 JSON 字符串，客户端从 content 里取
    text_blocks = [block for block in result.content if getattr(block, "type", "") == "text"]
    assert text_blocks, "没有收到文本结果"
    payload = json.loads(text_blocks[0].text)
    assert payload["ok"] is True
    assert payload["data"]["value"] == 12.0
    assert payload["via"] == "mcp", "服务端应把 via 标记为 mcp"


def test_工具报错以结果形式返回而不是抛异常() -> None:
    async def scenario():
        async with mcp.Client(_params()) as client:
            return await client.call_tool("calculate", {"expression": "__import__('os')"})

    result = _run(scenario)
    text_blocks = [block for block in result.content if getattr(block, "type", "") == "text"]
    payload = json.loads(text_blocks[0].text)
    assert payload["ok"] is False
    assert payload["hint"], "失败结果必须带自纠提示"


def test_未知工具返回错误而非崩溃() -> None:
    async def scenario():
        async with mcp.Client(_params()) as client:
            return await client.call_tool("does_not_exist", {})

    assert _run(scenario).is_error is True


def test_离线天气降级可用() -> None:
    async def scenario():
        async with mcp.Client(_params()) as client:
            return await client.call_tool("get_weather", {"city": "北京", "days": 2})

    result = _run(scenario)
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is True
    assert payload["data"]["source"] == "offline-sample"
    assert len(payload["data"]["days"]) == 2


def test_生成图片能返回产物路径() -> None:
    async def scenario():
        async with mcp.Client(_params()) as client:
            return await client.call_tool(
                "make_meme", {"text": "测试", "style": "note"}
            )

    payload = json.loads(_run(scenario).content[0].text)
    assert payload["ok"] is True
    assert payload["artifacts"][0]["kind"] == "image"
    assert Path(payload["artifacts"][0]["path"]).is_file()
