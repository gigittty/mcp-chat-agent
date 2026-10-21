"""Agent 主循环（ReAct 风格）。

一轮对话的流程：

    Thought（规划） → Action（执行工具） → Observation（读结果） → 再循环 → Final

三条硬性保证
-----------
1. **一定会「说」**：即使只调了一次工具，最后也要产出自然语言回复
2. **一定会「做」**：需要外部信息时绝不靠编，而是真的调用工具
3. **绝不崩**：工具异常、超时、规划失败都收敛成可展示的结果

多步是「真多步」：每次循环只执行一个动作，下一步基于上一步的真实返回值
重新规划。所以「查天气 → 画图」里的图，画的是刚刚查到的数据。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent import persona
from agent.config import Settings, get_settings
from agent.contracts import PlanDecision, ToolCall, ToolResult
from agent.mcp_host.channel import ToolChannel
from agent.memory import Memory
from agent.planner.base import PlanContext, Planner
from agent.planner.mock_planner import MockPlanner
from agent.sandbox import state_root

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    reply: str
    kind: str = "final"                     # final | ask | max_steps | error
    observations: list[ToolResult] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    @property
    def artifacts(self) -> list[Any]:
        return [artifact for result in self.observations for artifact in result.artifacts]


class Observer(Protocol):
    """界面层订阅执行过程。CLI 与 Streamlit 各实现一份。"""

    def on_thought(self, text: str, step: int) -> None: ...
    def on_action(self, call: ToolCall, step: int) -> None: ...
    def on_observation(self, call: ToolCall, result: ToolResult, step: int) -> None: ...
    def on_final(self, text: str) -> None: ...


class Agent:
    def __init__(
        self,
        planner: Planner | None = None,
        channel: ToolChannel | None = None,
        memory: Memory | None = None,
        settings: Settings | None = None,
        observer: Observer | None = None,
        max_steps: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.memory = memory or Memory()
        self.channel = channel or ToolChannel(self.settings.mcp_channel)
        self.planner = planner or MockPlanner()
        self.observer = observer
        self.max_steps = max_steps or self.settings.max_steps
        self._tools = self.channel.tools()

    # ------------------------------------------------------------------ 对外
    def tools(self):
        return self._tools

    def chat(self, user_text: str) -> TurnResult:
        text = (user_text or "").strip()
        if not text:
            reply = persona.greet("empty")
            return TurnResult(reply=reply, kind="ask")

        self.memory.add_user(text)
        learned = self.memory.learn_from(text)

        observations: list[ToolResult] = []
        steps: list[dict[str, Any]] = []
        ctx = PlanContext(
            user_text=text,
            tools=self._tools,
            history=self.memory.recent(),
            observations=observations,
            prefs=self.memory.prefs,
            pending=self.memory.pending,
            previous=self.memory.previous,
            step=0,
        )

        started = time.perf_counter()
        for step in range(self.max_steps):
            ctx.step = step
            try:
                decision = self.planner.plan(ctx)
            except Exception as exc:  # noqa: BLE001
                logger.exception("规划失败")
                return self._finish(
                    persona.apologize(f"规划时出错了：{type(exc).__name__}"),
                    "error", observations, steps, started,
                )

            self._emit("on_thought", decision.thought, step)
            steps.append({"step": step, "thought": decision.thought, "debug": _short_debug(decision.debug)})

            # 反问：把待补槽位存进记忆，下一轮接着做
            if decision.ask_user:
                self.memory.pending = decision.debug.get("pending")
                self.memory.add_assistant(decision.ask_user)
                self._emit("on_final", decision.ask_user)
                return self._finish(decision.ask_user, "ask", observations, steps, started, learned)

            # 终局回复
            if decision.final_text is not None:
                self.memory.clear_pending()
                self.memory.add_assistant(decision.final_text)
                self._emit("on_final", decision.final_text)
                return self._finish(decision.final_text, "final", observations, steps, started, learned)

            if not decision.calls:
                # 规划器什么都没给，兜一句
                reply = persona.chat(text)
                self.memory.add_assistant(reply)
                self._emit("on_final", reply)
                return self._finish(reply, "final", observations, steps, started, learned)

            failed = False
            for call in decision.calls:
                arguments = resolve_refs(call.arguments, observations)
                call.arguments = arguments
                self._emit("on_action", call, step)
                result = self._invoke(call)
                observations.append(result)
                ctx.last_tool = call.name
                self.memory.add_tool(call, result)
                self._emit("on_observation", call, result, step)
                steps[-1]["call"] = {"name": call.name, "arguments": arguments}
                steps[-1]["result"] = {
                    "ok": result.ok,
                    "text": result.text[:600],
                    "error": result.error,
                    "via": result.via,
                    "elapsed_ms": result.elapsed_ms,
                }
                if not result.ok:
                    failed = True
                    break
            if failed:
                continue        # 回到规划器，让它看到失败原因后自纠

        reply = "本龙做到这里就卡住了…要不要换个说法再试一次？"
        self.memory.add_assistant(reply)
        return self._finish(reply, "max_steps", observations, steps, started, learned)

    # ------------------------------------------------------------------ 内部
    def _invoke(self, call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        try:
            result = self.channel.invoke(call.name, call.arguments)
        except Exception as exc:  # noqa: BLE001
            logger.exception("工具调用异常")
            result = ToolResult.failure(
                f"调用 {call.name} 出错：{type(exc).__name__}: {exc}",
                hint="可以换个参数再试",
            )
        if result.elapsed_ms == 0:
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        result.tool_name = call.name
        return result

    def _finish(
        self,
        reply: str,
        kind: str,
        observations: list[ToolResult],
        steps: list[dict[str, Any]],
        started: float,
        learned: list[str] | None = None,
    ) -> TurnResult:
        trace = {
            "mode": self.settings.agent_mode,
            "channel": self.channel.active_mode,
            "fallback_reason": self.channel.fallback_reason,
            "planner": getattr(self.planner, "name", type(self.planner).__name__),
            "steps": steps,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "learned": learned or [],
        }
        self._write_trace(trace)
        return TurnResult(reply=reply, kind=kind, observations=observations, trace=trace)

    @staticmethod
    def _write_trace(trace: dict[str, Any]) -> None:
        try:
            target = state_root()
            target.mkdir(parents=True, exist_ok=True)
            (target / "last_trace.json").write_text(
                json.dumps(trace, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _emit(self, hook: str, *args: Any) -> None:
        if self.observer is None:
            return
        callback: Callable[..., None] | None = getattr(self.observer, hook, None)
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 - 界面出错不该影响智能体
            logger.debug("界面回调 %s 抛异常", hook, exc_info=True)

    # ------------------------------------------------------------------ 关闭
    def close(self) -> None:
        self.channel.close()


def _short_debug(debug: dict[str, Any]) -> dict[str, Any]:
    """trace 里只留打分明细与槽位，去掉大对象。"""
    keep = {}
    for key in ("segments", "scores", "slots", "repair", "resume", "pending"):
        if key in debug:
            keep[key] = debug[key]
    return keep


def resolve_refs(value: Any, observations: list[ToolResult]) -> Any:
    """解析 ``$ref:steps[-1].data.xxx`` 形式的跨步引用。

    规则式规划器在抽取槽位时就已经把数据填好了，所以通常用不到；
    但大模型可能直接产出引用字符串，这里兜住。
    """
    if isinstance(value, str) and value.startswith("$ref:"):
        return _resolve_path(value[5:], observations)
    if isinstance(value, list):
        return [resolve_refs(item, observations) for item in value]
    if isinstance(value, dict):
        return {key: resolve_refs(item, observations) for key, item in value.items()}
    return value


def _resolve_path(path: str, observations: list[ToolResult]) -> Any:
    import re

    match = re.match(r"steps\[(-?\d+)\]\.?(.*)", path.strip())
    if not match or not observations:
        return None
    index = int(match.group(1))
    if index < 0:
        index = len(observations) + index
    if not 0 <= index < len(observations):
        return None
    node: Any = observations[index]
    for part in [p for p in match.group(2).split(".") if p]:
        if part == "data":
            node = getattr(node, "data", None)
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            node = getattr(node, part, None)
        if node is None:
            return None
    return node
