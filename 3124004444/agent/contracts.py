"""智能体的核心数据结构。

这一层不依赖任何第三方库、不依赖 UI、不依赖 MCP——
所以工具、规划器、执行器可以在任何环境里被单独测试。

关键约定
--------
1. ``ToolSpec.input_schema`` 是工具参数的**唯一真源**。
   MCP 侧的封装、OpenAI function calling 的格式、规划器的槽位抽取，
   全都从它派生，不允许另写一份。
2. 工具的失败不是异常，而是 ``ToolResult(ok=False)``。
   失败结果里的 ``hint`` 是给规划器的「怎么改」提示，用于自纠重试。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class Artifact:
    """工具产出的文件（图片、文本文件等）。"""

    kind: str            # image | text | file
    path: str            # 绝对路径（已过沙箱校验）
    mime: str = "application/octet-stream"
    caption: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path, "mime": self.mime, "caption": self.caption}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            kind=str(data.get("kind", "file")),
            path=str(data.get("path", "")),
            mime=str(data.get("mime", "application/octet-stream")),
            caption=str(data.get("caption", "")),
        )


@dataclass
class ToolSpec:
    """工具声明。名字、描述、参数 schema 都在这里，是唯一真源。"""

    name: str                      # snake_case 全局唯一
    title: str                     # 中文短名，给界面用
    description: str               # 给规划器/大模型看的说明，需写清「什么时候该用」
    input_schema: dict[str, Any]   # JSON Schema，根节点必须是 object
    tags: list[str] = field(default_factory=list)      # 粗分类，规划器打分用
    produces: list[str] = field(default_factory=list)  # 可被后续步骤引用的字段
    examples: list[dict[str, Any]] = field(default_factory=list)

    def required_params(self) -> list[str]:
        return list(self.input_schema.get("required", []))

    def properties(self) -> dict[str, Any]:
        return dict(self.input_schema.get("properties", {}))


@dataclass
class ToolCall:
    """规划器产出的一次工具调用意图。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = field(default_factory=new_call_id)
    reason: str = ""             # 为什么调它，用于展示「思考过程」

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments, "call_id": self.call_id, "reason": self.reason}


@dataclass
class ToolResult:
    """工具执行结果。ok=False 也是正常返回值，不是异常。"""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    text: str = ""                     # 自然语言摘要，直接喂给规划器当 Observation
    error: str | None = None
    hint: str | None = None            # 给规划器的自纠提示
    artifacts: list[Artifact] = field(default_factory=list)
    elapsed_ms: int = 0
    via: str = "builtin"               # builtin | mcp —— 用来证明「真的走了 MCP」
    #: 产生这个结果的工具名。由执行层填，大模型规划器需要它来拼 tool 消息。
    tool_name: str = ""

    # ---- 构造快捷方式 ----
    @classmethod
    def success(cls, text: str, data: dict[str, Any] | None = None, **kwargs: Any) -> "ToolResult":
        return cls(ok=True, text=text, data=data or {}, **kwargs)

    @classmethod
    def failure(cls, error: str, hint: str | None = None, **kwargs: Any) -> "ToolResult":
        return cls(ok=False, error=error, hint=hint, text=error, **kwargs)

    # ---- 跨进程序列化（MCP 通道用）----
    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "text": self.text,
            "error": self.error,
            "hint": self.hint,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "elapsed_ms": self.elapsed_ms,
            "via": self.via,
            "tool_name": self.tool_name,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ToolResult":
        return cls(
            ok=bool(payload.get("ok", False)),
            data=dict(payload.get("data") or {}),
            text=str(payload.get("text", "")),
            error=payload.get("error"),
            hint=payload.get("hint"),
            artifacts=[Artifact.from_dict(a) for a in payload.get("artifacts") or []],
            elapsed_ms=int(payload.get("elapsed_ms") or 0),
            via=str(payload.get("via", "builtin")),
            tool_name=str(payload.get("tool_name", "")),
        )

    @classmethod
    def from_json(cls, raw: str) -> "ToolResult":
        return cls.from_dict(json.loads(raw))


@dataclass
class Message:
    """一条对话消息。"""

    role: str                                   # user | assistant | tool | system
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class PlanDecision:
    """规划器对某一轮的决定。

    三者互斥优先级：ask_user > final_text > calls
    """

    thought: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    final_text: str | None = None
    ask_user: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return bool(self.ask_user) or self.final_text is not None
