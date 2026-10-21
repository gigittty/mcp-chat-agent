"""工具注册表 —— 全项目「有哪些工具」的唯一真源。

每个工具模块只需提供两个东西::

    SPEC: ToolSpec                 # 声明（名字/描述/参数 schema）
    def run(args: dict) -> ToolResult: ...

注册表负责把模块加载进来，并通过 ``invoke()`` 统一调用。
MCP Server 也从这里取工具清单，因此不存在「业务逻辑写两遍」。

``TOOL_MODULES`` 是一个**随开发进度增长**的列表：还没实现的工具不会被 import，
所以任何一次提交后的代码树都是可运行的。
"""

from __future__ import annotations

import importlib
import inspect
import re
import time
from typing import Callable

from agent.contracts import ToolResult, ToolSpec
from agent.errors import ToolError

#: 已实现的工具模块（相对 agent.tools）。新增工具时在末尾追加。
TOOL_MODULES: list[str] = []

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class ToolRegistry:
    """工具登记与调用。线程安全（读取为主，加载只做一次）。"""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._runners: dict[str, Callable[[dict], ToolResult]] = {}
        self._loaded = False

    # ---------- 注册 ----------
    def register(self, spec: ToolSpec, runner: Callable[[dict], ToolResult]) -> None:
        self._validate_spec(spec)
        if spec.name in self._specs:
            raise ValueError(f"工具名重复：{spec.name}")
        if not callable(runner):
            raise TypeError(f"工具 {spec.name} 的 run 不可调用")
        self._specs[spec.name] = spec
        self._runners[spec.name] = runner

    @staticmethod
    def _validate_spec(spec: ToolSpec) -> None:
        if not _NAME_RE.match(spec.name):
            raise ValueError(f"工具名必须是 snake_case：{spec.name!r}")
        schema = spec.input_schema
        if not isinstance(schema, dict):
            raise ValueError(f"{spec.name}: input_schema 必须是 dict")
        if schema.get("type") != "object":
            # MCP 2.x 对 tools/list 做严格校验，根节点不是 object 会直接失败
            raise ValueError(f"{spec.name}: input_schema.type 必须是 'object'")
        required = schema.get("required", [])
        props = schema.get("properties", {})
        missing = [key for key in required if key not in props]
        if missing:
            raise ValueError(f"{spec.name}: required 里的字段未在 properties 中声明：{missing}")

    # ---------- 加载 ----------
    def load(self, modules: list[str] | None = None) -> "ToolRegistry":
        """导入并注册工具模块。重复调用无副作用。"""
        if self._loaded and modules is None:
            return self
        for module_name in modules if modules is not None else TOOL_MODULES:
            full_name = f"agent.tools.{module_name}"
            module = importlib.import_module(full_name)
            spec = getattr(module, "SPEC", None)
            runner = getattr(module, "run", None)
            if not isinstance(spec, ToolSpec):
                raise TypeError(f"{full_name} 缺少 SPEC: ToolSpec")
            if not inspect.isfunction(runner):
                raise TypeError(f"{full_name} 缺少 run(args) 函数")
            if spec.name not in self._specs:
                self.register(spec, runner)
        self._loaded = True
        return self

    def reset(self) -> None:
        self._specs.clear()
        self._runners.clear()
        self._loaded = False

    # ---------- 查询 ----------
    def all(self) -> list[ToolSpec]:
        self.load()
        return list(self._specs.values())

    def names(self) -> list[str]:
        return [spec.name for spec in self.all()]

    def get(self, name: str) -> ToolSpec | None:
        self.load()
        return self._specs.get(name)

    def __len__(self) -> int:
        return len(self.all())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._specs

    # ---------- 调用 ----------
    def invoke(self, name: str, arguments: dict) -> ToolResult:
        """执行工具，永不抛异常（除编程错误外一律转成 ToolResult）。"""
        self.load()
        runner = self._runners.get(name)
        if runner is None:
            return ToolResult.failure(
                f"没有名为 {name} 的工具",
                hint=f"可用工具：{', '.join(sorted(self._specs)) or '（空）'}",
            )
        if not isinstance(arguments, dict):
            return ToolResult.failure("参数必须是对象", hint="请把参数写成 JSON 对象")

        started = time.perf_counter()
        try:
            result = runner(dict(arguments))
        except ToolError as exc:
            return ToolResult.failure(str(exc), hint=exc.hint, elapsed_ms=_elapsed(started))
        except Exception as exc:  # noqa: BLE001 - 工具内部错误不应让整个对话崩掉
            return ToolResult.failure(
                f"工具 {name} 执行出错：{type(exc).__name__}: {exc}",
                hint="可以换个参数再试，或改用其他工具",
                elapsed_ms=_elapsed(started),
            )

        if not isinstance(result, ToolResult):
            return ToolResult.failure(f"工具 {name} 返回值类型错误：{type(result).__name__}")
        if result.elapsed_ms == 0:
            result.elapsed_ms = _elapsed(started)
        return result


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """进程内单例。"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
