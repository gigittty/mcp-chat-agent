"""工具格式转换：``ToolSpec`` ↔ OpenAI function calling。

这一层的存在让「工具只定义一次」成立：
``ToolSpec.input_schema`` 是 JSON Schema，OpenAI 的 ``function.parameters``
也是 JSON Schema，所以几乎是直接搬运，不需要另写一份声明。
"""

from __future__ import annotations

import json
from typing import Any

from agent.contracts import ToolCall, ToolSpec


def to_openai_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
        }
        for spec in specs
    ]


def parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    """把模型返回的 tool_calls 解析成内部结构。

    参数是 JSON 字符串，模型偶尔会给出不合法 JSON（尾逗号、单引号等），
    这里做一次宽松修复，修不好就留空参数让工具自己报错。
    """
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments")
        calls.append(
            ToolCall(
                name=name,
                arguments=_loads_arguments(arguments),
                call_id=str(raw.get("id") or ""),
            )
        )
    return calls


def _loads_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    text = str(raw).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    # 宽松修复：去掉尾逗号
    import re

    patched = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        parsed = json.loads(patched)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def tool_result_message(call_id: str, payload: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": payload}


def assistant_tool_call_message(calls: list[ToolCall], content: str | None = None) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call.call_id or f"call_{index}",
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for index, call in enumerate(calls)
        ],
    }
