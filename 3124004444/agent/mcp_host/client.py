"""MCP 客户端（Host 侧）。

用官方 SDK 的 ``Client(StdioServerParameters(...))`` 起一个子进程，
通过 stdin/stdout 说 JSON-RPC，然后：

- ``list_tools()`` → 得到工具清单（**真正的动态发现**，不是本地硬编码）
- ``call_tool()``  → 执行工具

几个容易踩的点，代码里都处理了：

1. **子进程不继承父进程环境**。SDK 只给 allow-list（PATH/HOME 等），
   所以 ``PYTHONPATH``/``PYTHONUTF8`` 必须显式传，否则子进程 import 不到
   我们的包、中文还会乱码。
2. **返回值形态**：服务端统一返回 JSON 字符串，因此从 ``content`` 里取
   ``TextContent.text`` 再 ``json.loads``，而不是依赖 ``structured_content``
   （实测返回 dict 时它是 None）。
3. **``is_error=True`` 不抛异常**，是正常结果，需要转成 ``ToolResult(ok=False)``。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from agent import CODE_ROOT
from agent.contracts import ToolResult, ToolSpec
from agent.runtime import AsyncRuntime, get_runtime

DEFAULT_TIMEOUT = 30.0


class McpUnavailable(RuntimeError):
    """mcp 包缺失或子进程连不上。"""


def _server_params(timeout: float = DEFAULT_TIMEOUT):
    try:
        from mcp import StdioServerParameters
    except ImportError as exc:  # pragma: no cover - 取决于安装环境
        raise McpUnavailable("未安装 mcp 包，无法使用 MCP 通道") from exc

    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(CODE_ROOT),
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "mcp_server"],
        cwd=str(CODE_ROOT),
        env=env,
    )


def _text_of(result: Any) -> str:
    """从 CallToolResult 里取出文本内容。"""
    blocks = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        if getattr(block, "type", "") == "text":
            parts.append(str(getattr(block, "text", "")))
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return ""


class McpHost:
    """管理一个 stdio MCP 会话。"""

    def __init__(self, runtime: AsyncRuntime | None = None, code_root: Path | None = None) -> None:
        self._runtime = runtime or get_runtime()
        self._code_root = code_root or CODE_ROOT
        self._context: Any = None
        self._client: Any = None
        self._tools: list[ToolSpec] = []
        self.server_info: str = ""
        self.protocol_version: str = ""

    # ------------------------------------------------------------------ 连接
    def connect(self, timeout: float = DEFAULT_TIMEOUT) -> list[ToolSpec]:
        try:
            import mcp  # noqa: F401
        except ImportError as exc:
            raise McpUnavailable("未安装 mcp 包") from exc

        params = _server_params(timeout)
        listed = self._runtime.submit(self._connect(params), timeout=timeout)
        # 注意：ListToolsResult 是 pydantic 模型，直接迭代会得到 (字段名, 值) 元组，
        # 必须显式取 .tools
        self._tools = [self._to_spec(tool) for tool in listed.tools]
        return self._tools

    async def _connect(self, params: Any):
        from mcp import Client

        self._context = Client(params)
        self._client = await self._context.__aenter__()
        self.server_info = getattr(getattr(self._client, "server_info", None), "name", "") or ""
        self.protocol_version = str(getattr(self._client, "protocol_version", "") or "")
        return await self._client.list_tools()

    @staticmethod
    def _to_spec(tool: Any) -> ToolSpec:
        schema = getattr(tool, "input_schema", None) or {"type": "object", "properties": {}}
        return ToolSpec(
            name=str(tool.name),
            title=str(getattr(tool, "title", None) or tool.name),
            description=str(getattr(tool, "description", "") or ""),
            input_schema=schema,
        )

    @property
    def tools(self) -> list[ToolSpec]:
        return list(self._tools)

    # ------------------------------------------------------------------ 调用
    def call(self, name: str, arguments: dict, timeout: float = 60.0) -> ToolResult:
        if self._client is None:
            return ToolResult.failure("MCP 会话尚未建立", hint="请先 connect()")
        try:
            raw = self._runtime.submit(self._client.call_tool(name, arguments), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - 传输层错误也要变成可展示结果
            return ToolResult.failure(
                f"MCP 调用失败：{type(exc).__name__}: {exc}",
                hint="可能是子进程挂了，可以切到内置通道（--channel builtin）",
            )

        text = _text_of(raw)
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return ToolResult(
                ok=not bool(getattr(raw, "is_error", False)),
                text=text or "（工具没有返回内容）",
                via="mcp",
            )

        result = ToolResult.from_dict(payload)
        result.via = "mcp"          # 以服务端盖章为准，但确保标记正确
        if getattr(raw, "is_error", False):
            result.ok = False
        return result

    # ------------------------------------------------------------------ 关闭
    def close(self) -> None:
        if self._context is None:
            return
        try:
            self._runtime.submit(self._context.__aexit__(None, None, None), timeout=10.0)
        except Exception:  # noqa: BLE001 - 关闭失败不应该影响退出
            pass
        finally:
            self._context = None
            self._client = None
