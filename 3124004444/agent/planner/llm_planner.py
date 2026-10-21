"""大模型规划器（OpenAI 兼容 function calling）。

与规则式规划器返回同一种 ``PlanDecision``，所以主循环一行都不用改。
这是「换模型零改内核」的结构证据：

    planner = MockPlanner()        # 没 Key
    planner = LLMPlanner(settings) # 有 Key

两者在 :class:`agent.agent.Agent` 眼里没有区别。

另外：模型挂了/Key 错了不会让程序崩——自动退回规则式规划器，
保证答辩现场「最差也能演示」。
"""

from __future__ import annotations

import logging

from agent import persona
from agent.config import Settings
from agent.contracts import PlanDecision, ToolCall, ToolResult
from agent.llm.client import LLMError, OpenAICompatClient
from agent.llm.tools_format import (
    assistant_tool_call_message,
    parse_tool_calls,
    to_openai_tools,
    tool_result_message,
)
from agent.planner.base import PlanContext, Planner
from agent.planner.mock_planner import MockPlanner
from agent.planner.validate_schema import validate

logger = logging.getLogger(__name__)

MAX_HISTORY = 6


class LLMPlanner(Planner):
    name = "llm"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAICompatClient(settings)
        self._fallback = MockPlanner()
        self.last_error: str | None = None

    # ------------------------------------------------------------------ 入口
    def plan(self, ctx: PlanContext) -> PlanDecision:
        if not self.client.configured:
            self.last_error = "未配置 API Key"
            return self._fallback.plan(ctx)

        try:
            message = self.client.chat(self._build_messages(ctx), to_openai_tools(ctx.tools))
        except LLMError as exc:
            self.last_error = str(exc)
            logger.warning("大模型不可用，本轮退回规则式规划：%s", exc)
            decision = self._fallback.plan(ctx)
            decision.thought = f"（大模型不可用，改用离线规则）{decision.thought}"
            decision.debug["llm_error"] = str(exc)
            return decision

        self.last_error = None
        return self._to_decision(message, ctx)

    # ------------------------------------------------------------------ 转换
    def _to_decision(self, message: dict, ctx: PlanContext) -> PlanDecision:
        calls = parse_tool_calls(message)
        content = (message.get("content") or "").strip()

        if calls:
            call = calls[0]              # 每步只执行一个动作，保持主循环的节奏
            spec = ctx.spec_of(call.name)
            if spec is not None:
                ok, fixed, issues = validate(spec.input_schema, call.arguments)
                call.arguments = fixed
                if not ok:
                    return PlanDecision(
                        thought=content or f"准备调用 {call.name}",
                        ask_user=f"模型给的参数不太对（{issues[0] if issues else '缺少必填项'}），"
                                 "能再补充一下吗？",
                        debug={"llm_issues": issues},
                    )
            return PlanDecision(
                thought=content or f"调用「{spec.title if spec else call.name}」",
                calls=[call],
                debug={"llm_raw_calls": len(calls)},
            )

        if content:
            return PlanDecision(thought="直接回答", final_text=content, debug={})

        return PlanDecision(final_text=persona.chat(ctx.user_text), debug={"llm_empty": True})

    # ------------------------------------------------------------------ 拼消息
    def _build_messages(self, ctx: PlanContext) -> list[dict]:
        tool_lines = [f"- {spec.name}: {spec.description.split('。')[0]}" for spec in ctx.tools]
        messages: list[dict] = [{"role": "system", "content": persona.system_prompt(tool_lines)}]

        history = list(ctx.history)
        if history and history[-1].role == "user":
            history = history[:-1]      # 当前这句下面单独加
        for item in history[-MAX_HISTORY:]:
            if item.role in ("user", "assistant") and item.content:
                messages.append({"role": item.role, "content": item.content})

        messages.append({"role": "user", "content": ctx.user_text})

        # 本轮的 ReAct scratchpad：用工具消息把已执行的结果回填给模型
        for index, observation in enumerate(ctx.observations):
            call_id = f"call_{index}"
            synthetic = ToolCall(name=observation.tool_name or "tool", arguments={}, call_id=call_id)
            messages.append(assistant_tool_call_message([synthetic]))
            messages.append(tool_result_message(call_id, observation.to_json()))
        return messages
