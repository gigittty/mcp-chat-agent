"""常驻事件循环。

为什么必须有这个东西
-------------------
项目的异步代码（MCP 客户端）要在一个**持续存在**的事件循环里跑，原因有两个：

1. **Windows 的坑**：默认的 Selector 事件循环无法 ``asyncio.create_subprocess_exec``，
   而起 MCP 子进程正是用它。必须换成 Proactor。
2. **Streamlit 的坑**：Streamlit 每次交互都在同一个线程里执行，那里可能已经有
   正在运行的 loop，``asyncio.run()`` 会直接抛
   ``RuntimeError: asyncio.run() cannot be called from a running event loop``。

解法是一份代码解决两个问题：开一个后台线程，里面建一个常驻 loop，
外部通过 ``run_coroutine_threadsafe`` 提交协程并同步等结果。
"""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


class AsyncRuntime:
    """后台线程 + 常驻事件循环。进程内单例使用。"""

    def __init__(self, name: str = "agent-loop") -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> "AsyncRuntime":
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            self._thread = threading.Thread(target=self._serve, name=self._name, daemon=True)
            self._thread.start()
        self._ready.wait(timeout=5.0)
        return self

    def _serve(self) -> None:
        if sys.platform == "win32":
            # 子进程支持只在 Proactor 上可用
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    # ------------------------------------------------------------------ 调用
    def submit(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """在常驻循环里执行协程，同步等待结果。"""
        if self._closed:
            raise RuntimeError("AsyncRuntime 已关闭，无法再提交任务")
        self.start()
        loop = self._loop
        if loop is None:
            raise RuntimeError("事件循环启动失败")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout)

    def shutdown(self, timeout: float = 10.0) -> None:
        """停止循环并等线程退出。必须先关掉 MCP 会话再调用，否则子进程会残留。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, thread = self._loop, self._thread

        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._loop = None
        self._thread = None


_default_runtime: AsyncRuntime | None = None


def get_runtime() -> AsyncRuntime:
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = AsyncRuntime().start()
    return _default_runtime
