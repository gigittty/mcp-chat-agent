"""工具执行通道：MCP 优先，失败自动降级为进程内直调。

三种模式
-------
- ``auto``（默认）：先尝试 MCP，握手失败就降级，并在整轮对话里记住这个决定
- ``mcp``：强制走协议，连不上直接报错（答辩时用来证明「真的在用 MCP」）
- ``builtin``：完全不加载 mcp 包，纯函数调用（连装不上 mcp 也能演示）

两种模式下 ``ToolResult`` 的结构完全一致，所以主循环不需要分支。
唯一的区别是 ``via`` 字段，界面会把它显示出来——这也是「真的走了 MCP」
最直观的证据。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.contracts import ToolResult, ToolSpec
from agent.registry import get_registry
from agent.runtime import AsyncRuntime

logger = logging.getLogger(__name__)

CHANNEL_BUILTIN = "builtin"
CHANNEL_MCP = "mcp"


class ToolChannel:
    def __init__(self, mode: str = "auto", runtime: AsyncRuntime | None = None, code_root: Any = None) -> None:
        self.requested_mode = mode if mode in ("auto", "mcp", "builtin") else "auto"
        self.runtime = runtime
        self.code_root = code_root
        self.active_mode = CHANNEL_BUILTIN
        self.fallback_reason: str | None = None
        self._host: Any = None
        self._tools: list[ToolSpec] = []
        self._started = False

    # ------------------------------------------------------------------ 启动
    def start(self) -> list[ToolSpec]:
        if self._started:
            return self._tools
        self._started = True

        if self.requested_mode == "builtin":
            self.active_mode = CHANNEL_BUILTIN
            self._tools = get_registry().all()
            return self._tools

        try:
            from agent.mcp_host.client import McpHost

            host = McpHost(runtime=self.runtime, code_root=self.code_root)
            self._tools = host.connect()
            self._host = host
            self.active_mode = CHANNEL_MCP
            logger.info("已连接 MCP 服务端：%s（协议 %s，%d 个工具）",
                        host.server_info, host.protocol_version, len(self._tools))
            return self._tools
        except Exception as exc:  # noqa: BLE001
            self.fallback_reason = f"{type(exc).__name__}: {exc}"
            if self.requested_mode == "mcp":
                raise RuntimeError(
                    f"强制 MCP 通道但连接失败：{self.fallback_reason}\n"
                    "排查建议：pip install -U 'mcp>=2.3'；或改用 --channel builtin"
                ) from exc
            logger.warning("MCP 通道不可用，降级为内置直调：%s", self.fallback_reason)
            self.active_mode = CHANNEL_BUILTIN
            self._tools = get_registry().all()
            return self._tools

    # ------------------------------------------------------------------ 查询
    def tools(self) -> list[ToolSpec]:
        return self.start()

    def get_spec(self, name: str) -> ToolSpec | None:
        return next((spec for spec in self.tools() if spec.name == name), None)

    @property
    def label(self) -> str:
        if self.active_mode == CHANNEL_MCP:
            return "MCP 协议（stdio 子进程）"
        return "进程内直调"

    # ------------------------------------------------------------------ 调用
    def invoke(self, name: str, arguments: dict) -> ToolResult:
        if not self._started:
            self.start()

        if self.active_mode == CHANNEL_MCP and self._host is not None:
            result = self._host.call(name, arguments)
            if not result.ok and result.hint and "子进程" in result.hint:
                # 传输层彻底坏了：本次进程内不再走 MCP，避免每轮都等超时
                logger.warning("MCP 调用失败，后续改用内置通道")
                self.fallback_reason = result.error
                self.active_mode = CHANNEL_BUILTIN
                return get_registry().invoke(name, arguments)
            return result

        return get_registry().invoke(name, arguments)

    # ------------------------------------------------------------------ 关闭
    def close(self) -> None:
        if self._host is not None:
            self._host.close()
            self._host = None

    def __enter__(self) -> "ToolChannel":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
