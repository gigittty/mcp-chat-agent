"""统一异常类型。

设计原则：工具的失败是一种「可展示的结果」，不是程序崩溃。
工具内部可以把异常收敛成 ToolResult(ok=False)，但底层仍保留明确异常类型便于定位。
"""

from __future__ import annotations


class AgentError(Exception):
    """本项目的异常基类。"""


class ToolError(AgentError):
    """工具执行失败（可预期，带人话提示）。"""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message


class SandboxViolation(ToolError):
    """试图访问沙箱目录之外的文件。"""


class ConfigError(AgentError):
    """配置非法。"""


class PlannerError(AgentError):
    """规划器无法产出合法计划。"""
