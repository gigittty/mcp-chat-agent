"""MCP 工具封装。

这里是「薄壳」：每个函数只做**参数类型适配**，业务逻辑全部委托给
``agent.registry``。所以同一份工具实现，既服务 MCP 通道，也服务进程内直调。

为什么不用装饰器
----------------
本模块**不 import mcp**。好处有两个：
1. 没装 mcp 包时，``tests/test_schema_parity.py`` 仍能检查这里的函数签名
   与注册表 schema 是否一致——一致性保证不依赖于有没有装 mcp；
2. 依赖方向保持单向：``mcp_server`` → ``agent``，绝不反过来。

工具描述文案统一从 ``SPEC.description`` 注入（见文件末尾），
避免「schema 一份、说明又写一份」。
"""

from __future__ import annotations

from agent.contracts import ToolResult
from agent.registry import get_registry


def _call(tool_name: str, arguments: dict) -> str:
    """统一调用入口：执行工具并把结果序列化成 JSON 字符串。

    为什么返回字符串而不是 dict
    ---------------------------
    实测 mcp 2.3.0：返回 dict 时客户端的 ``structured_content`` 是 ``None``，
    只有返回 str 才有 ``{"result": ...}``。统一返回 JSON 字符串后，
    客户端从 ``content[0].text`` 解析，两种通道拿到的结构完全一致。
    """
    result = get_registry().invoke(tool_name, arguments)
    if not isinstance(result, ToolResult):
        return ToolResult.failure("内部错误：工具返回了非法结果").to_json()
    result.via = "mcp"          # 由服务端盖章，客户端可直接据此证明「真的走了 MCP」
    return result.to_json()


def calculate(expression: str, precision: int = 4) -> str:
    """计算数学表达式。"""
    return _call("calculate", {"expression": expression, "precision": precision})


def fun_pick(mode: str, sides: int = 6, count: int = 1, options: list[str] | None = None) -> str:
    """做随机选择。"""
    payload: dict = {"mode": mode, "sides": sides, "count": count}
    if options is not None:
        payload["options"] = options
    return _call("fun_pick", payload)


def get_weather(city: str, days: int = 1, unit: str = "c") -> str:
    """查询城市天气。"""
    return _call("get_weather", {"city": city, "days": days, "unit": unit})


def todo(
    action: str,
    item: str | None = None,
    due: str | None = None,
    id: int | None = None,  # noqa: A002 - 参数名与 schema 字段保持一致，故保留 id
) -> str:
    """管理待办清单。"""
    payload: dict = {"action": action}
    if item is not None:
        payload["item"] = item
    if due is not None:
        payload["due"] = due
    if id is not None:
        payload["id"] = id
    return _call("todo", payload)


def make_chart(
    labels: list[str],
    values: list[float],
    title: str = "",
    kind: str = "bar",
    ylabel: str = "",
) -> str:
    """生成图表。"""
    return _call(
        "make_chart",
        {"labels": labels, "values": values, "title": title, "kind": kind, "ylabel": ylabel},
    )


def make_meme(text: str, subtext: str = "", style: str = "impact", palette: str = "warm") -> str:
    """生成梗图。"""
    return _call("make_meme", {"text": text, "subtext": subtext, "style": style, "palette": palette})


def make_ascii_art(text: str, scale: int = 1, fill: str = "#") -> str:
    """生成 ASCII 艺术字。"""
    return _call("make_ascii_art", {"text": text, "scale": scale, "fill": fill})


def files(action: str, path: str | None = None, content: str | None = None) -> str:
    """沙箱内文件读写。"""
    payload: dict = {"action": action}
    if path is not None:
        payload["path"] = path
    if content is not None:
        payload["content"] = content
    return _call("files", payload)


#: 注册顺序即 MCP tools/list 的顺序，保持与注册表一致便于比对
ALL = [
    calculate,
    fun_pick,
    get_weather,
    todo,
    make_chart,
    make_meme,
    make_ascii_art,
    files,
]


def _wire_docstrings() -> None:
    """把 SPEC.description 注入函数 docstring。

    MCP 用 docstring 作为工具描述，这样「描述」也只有一份来源。
    """
    registry = get_registry()
    for function in ALL:
        spec = registry.get(function.__name__)
        if spec is not None:
            function.__doc__ = spec.description


_wire_docstrings()
