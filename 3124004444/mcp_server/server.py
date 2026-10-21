"""MCP 服务端装配。

只做一件事：把 :mod:`mcp_server.wrappers` 里的薄封装挂到一个
:class:`mcp.server.MCPServer` 实例上，然后交给 ``mcp.run()`` 以 stdio 运行。

注意 mcp 2.x 的几处硬性约束（1.x 教程会踩坑）：
- 高层类叫 ``MCPServer``，``mcp.server.fastmcp.FastMCP`` 已被删除
- 传输层配置属于 ``run()``，不是构造函数（``MCPServer("x", port=9000)`` 会 TypeError）
- ``tools/list`` 会严格校验 schema，根节点必须是 ``{"type": "object"}``
"""

from __future__ import annotations

from mcp.server import MCPServer

from mcp_server import wrappers

INSTRUCTIONS = (
    "Milk Dragon 是一个会说也会做的中文聊天助手。"
    "它可以把用户指令转成真实的工具调用：计算数学、查询天气、生成图表与梗图、"
    "管理待办清单、在沙箱目录里读写文件、做随机决策。"
    "每次工具调用返回 JSON，字段包含 ok / text / data / artifacts。"
)


def build_server() -> MCPServer:
    """构建并返回已注册全部工具的 MCP 服务端实例。"""
    server = MCPServer(
        "milk-dragon-agent",
        version="0.1.0",
        instructions=INSTRUCTIONS,
    )
    for function in wrappers.ALL:
        server.tool()(function)
    return server


#: 模块级实例：`mcp run` 会自动寻找名为 server / mcp / app 的对象
server = build_server()
