"""规划器抽象。

两个实现（规则式 / 大模型）共用这一个接口，返回同一种 :class:`PlanDecision`，
所以 :mod:`agent.agent` 的主循环里**只有一条执行路径**——
「换模型零改内核」是结构保证，不是口号。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent.contracts import Message, PlanDecision, ToolCall, ToolResult, ToolSpec


@dataclass
class PlanContext:
    """规划一次所需的所有信息。"""

    user_text: str
    tools: list[ToolSpec] = field(default_factory=list)
    history: list[Message] = field(default_factory=list)
    observations: list[ToolResult] = field(default_factory=list)   # 本轮已执行结果
    prefs: dict[str, Any] = field(default_factory=dict)            # 长期偏好
    step: int = 0
    last_tool: str | None = None        # 上一轮调了哪个工具（用于上下文延续加分）
    pending: dict[str, Any] | None = None  # 上一轮欠用户的槽位
    #: 上一轮的最后一个成功结果。跨轮引用「把刚才的结果存成文件」靠它。
    previous: ToolResult | None = None
    debug: bool = False

    def tool_names(self) -> list[str]:
        return [spec.name for spec in self.tools]

    def spec_of(self, name: str) -> ToolSpec | None:
        return next((spec for spec in self.tools if spec.name == name), None)

    def last_observation(self) -> ToolResult | None:
        return self.observations[-1] if self.observations else None


class Planner(ABC):
    """规划器接口。"""

    name: str = "base"

    @abstractmethod
    def plan(self, ctx: PlanContext) -> PlanDecision:
        """根据上下文决定下一步：调用工具、反问，还是直接回答。"""

    # 便于日志与调试
    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<{type(self).__name__} name={self.name}>"


def make_call(name: str, arguments: dict, reason: str = "") -> ToolCall:
    return ToolCall(name=name, arguments=arguments, reason=reason)
