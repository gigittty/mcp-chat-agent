"""``python -m mcp_server`` 入口：以 stdio 方式启动 MCP 服务端。

**stdout 就是协议通道**。任何写进 stdout 的杂物（一次 print、一行第三方库
warning）都会让客户端解析 JSON-RPC 帧失败，表现为「连接突然断掉、看不到原因」。
所以这里的规矩是：

- 所有日志走 logging → stderr + 文件（``data/state/mcp_server.log``）
- 本文件内不出现 print
"""

from __future__ import annotations

import logging
import sys


def _setup_logging() -> None:
    from agent import data_dir

    state_dir = data_dir() / "state"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        log_file = state_dir / "mcp_server.log"
    except OSError:
        log_file = None

    handlers: list[logging.Handler] = [logging.StreamHandler(stream=sys.stderr)]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def main() -> int:
    _setup_logging()
    logger = logging.getLogger("mcp_server")
    try:
        from mcp_server.server import server
    except Exception:  # noqa: BLE001 - 启动失败必须留下可查的日志
        logger.exception("MCP 服务端装配失败")
        return 1

    logger.info("MCP 服务端启动（stdio），Python %s", sys.version.split()[0])
    try:
        server.run()          # 默认 transport 就是 stdio
    except KeyboardInterrupt:
        logger.info("收到中断，正常退出")
    except Exception:  # noqa: BLE001
        logger.exception("MCP 服务端运行期异常")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
