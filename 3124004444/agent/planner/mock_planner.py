"""规则式规划器：不需要大模型也能听懂人话。

为什么要有它
-----------
答辩现场可能没有 API Key、没有网络。如果智能体只有大模型一个大脑，
那就等于「没 Key 就什么都不能做」。所以这里实现一个完全离线的规划器，
和 :class:`~agent.planner.llm_planner.LLMPlanner` 返回同一种
:class:`~agent.contracts.PlanDecision`，上层主循环完全不用改。

一次 ``plan()`` 只产出一个动作
---------------------------
这是「真 ReAct」而不是「假动画」的关键：多步请求（查天气 → 画图）
不是一次性生成两个调用，而是**每一步基于上一步的真实结果重新决策**。
所以「画柱状图」用的数据，真的是刚刚那次天气查询返回的。

用 ``len(ctx.observations)`` 判断进度，规划器自身无状态，
同一轮里被调用多次也不会错乱。
"""

from __future__ import annotations

from agent import persona
from agent.contracts import PlanDecision, ToolCall
from agent.planner.base import PlanContext, Planner
from agent.planner.lexicon import INTENTS, SLOT_ASK
from agent.planner.normalize import normalize, segment
from agent.planner.scorer import THRESHOLD_ACT, THRESHOLD_CLARIFY, ScoredIntent, score_text
from agent.planner.slots import extract, find_city, find_math_expression
from agent.planner.validate_schema import describe_issues, validate

_GREET_WORDS = ("你好", "您好", "嗨", "hi", "hello", "在吗", "在不在", "哈喽")
_HELP_WORDS = ("你能做什么", "你会什么", "会干什么", "能干什么", "怎么用", "帮助", "help", "功能")
_THANKS_WORDS = ("谢谢", "谢啦", "thanks", "thx", "太好了")


class MockPlanner(Planner):
    """基于词表打分 + 槽位抽取的离线规划器。"""

    name = "mock"

    # ------------------------------------------------------------------ 入口
    def plan(self, ctx: PlanContext) -> PlanDecision:
        text = normalize(ctx.user_text)
        if not text:
            return PlanDecision(final_text=persona.greet("empty"), thought="用户没说话？")

        segments = segment(text)
        debug: dict = {"segments": segments, "scores": {}, "slots": {}}

        # 1) 上一轮欠了一个槽位 → 这一轮先把它补上
        if ctx.pending and not ctx.observations:
            resumed = self._resume_pending(ctx, text, debug)
            if resumed is not None:
                return resumed

        # 2) 上一步失败了 → 尝试自纠一次，否则如实认错
        if ctx.observations and not ctx.observations[-1].ok:
            repaired = self._self_repair(ctx, segments, debug)
            if repaired is not None:
                return repaired
            last = ctx.observations[-1]
            return PlanDecision(
                thought="上一步没成功，先停下来跟用户说清楚。",
                ask_user=persona.apologize(last.error or "工具执行失败", last.hint),
                debug=debug,
            )

        # 3) 所有片段都处理完了 → 总结
        handled = len(ctx.observations)
        if handled >= len(segments):
            return PlanDecision(
                thought="所有步骤都完成了，整理一下结果。",
                final_text=persona.summarize(ctx.observations),
                debug=debug,
            )

        # 4) 规划当前片段
        return self._plan_segment(segments[handled], ctx, debug, index=handled)

    # ------------------------------------------------------------------ 片段规划
    def _plan_segment(self, segment_text: str, ctx: PlanContext, debug: dict, index: int) -> PlanDecision:
        scored = score_text(segment_text, ctx)
        debug["scores"][segment_text] = [item.summary() for item in scored[:4]]
        top = scored[0] if scored else None

        # 完全没有意图 → 闲聊（只在第一段时闲聊，避免答非所问）
        if top is None or top.score < THRESHOLD_CLARIFY:
            if index == 0:
                return PlanDecision(
                    thought="没识别出要做什么，当闲聊处理。",
                    final_text=self._smalltalk(segment_text, ctx),
                    debug=debug,
                )
            return PlanDecision(
                thought="剩下的话没有可执行的动作，先总结已有结果。",
                final_text=persona.summarize(ctx.observations),
                debug=debug,
            )

        # 有倾向但不够确定 → 反问，而不是瞎猜
        if top.score < THRESHOLD_ACT:
            return PlanDecision(
                thought=f"「{segment_text}」有点模糊，给用户几个选项。",
                ask_user=self._clarify(scored, ctx),
                debug=debug,
            )

        spec = ctx.spec_of(top.name)
        if spec is None:
            return PlanDecision(
                thought="识别出了意图但当前没有这个工具。",
                ask_user=f"本龙暂时还不会「{INTENTS[top.name].name}」这个技能呢。",
                debug=debug,
            )

        args, missing = extract(top.name, segment_text, ctx)
        ok, fixed, issues = validate(spec.input_schema, args)
        debug["slots"] = {"raw": args, "fixed": fixed, "issues": issues}

        if not ok:
            fields = missing or [
                issue.split("缺少必填参数 ", 1)[1]
                for issue in issues
                if issue.startswith("缺少必填参数 ")
            ]
            if fields:
                return self._ask_for(top.name, fixed, fields, debug)
            return PlanDecision(
                thought="参数校验没通过。",
                ask_user=f"这些参数本龙没处理明白：{describe_issues(issues)}",
                debug=debug,
            )

        thought = f"{persona.ack(segment_text)}用「{spec.title}」来做这一步。"
        call = ToolCall(name=top.name, arguments=fixed, reason=top.summary())
        return PlanDecision(thought=thought, calls=[call], debug=debug)

    # ------------------------------------------------------------------ 追问与续接
    def _ask_for(self, tool: str, args: dict, fields: list[str], debug: dict) -> PlanDecision:
        field = fields[0]
        question = SLOT_ASK.get((tool, field), f"还差一个「{field}」的信息，能补充一下吗？")
        debug["pending"] = {"name": tool, "args": args, "missing": fields}
        return PlanDecision(
            thought=f"还缺参数 {field}，先问用户。",
            ask_user=question,
            debug=debug,
        )

    def _resume_pending(self, ctx: PlanContext, text: str, debug: dict) -> PlanDecision | None:
        """用用户这一句补上一个槽位。"""
        pending = ctx.pending or {}
        tool = pending.get("name")
        missing = list(pending.get("missing") or [])
        args = dict(pending.get("args") or {})
        if not tool or not missing:
            return None

        spec = ctx.spec_of(tool)
        if spec is None:
            return None

        field = missing[0]
        value = self._value_for_field(field, text, ctx)
        debug["resume"] = {"tool": tool, "field": field, "value": value}
        if value in (None, ""):
            # 还是没听出来，再问一次（但换句话）
            return self._ask_for(tool, args, missing, debug)

        args[field] = value
        remaining = missing[1:]
        ok, fixed, issues = validate(spec.input_schema, args)
        if not ok:
            left = remaining or [
                issue.split("缺少必填参数 ", 1)[1]
                for issue in issues
                if issue.startswith("缺少必填参数 ")
            ]
            if left:
                return self._ask_for(tool, fixed, left, debug)
            return PlanDecision(ask_user=describe_issues(issues), debug=debug)

        thought = f"补齐了 {field}，继续用「{spec.title}」。"
        return PlanDecision(thought=thought, calls=[ToolCall(name=tool, arguments=fixed)], debug=debug)

    @staticmethod
    def _value_for_field(field: str, text: str, ctx: PlanContext) -> str | None:
        if field == "city":
            return find_city(text, ctx.prefs)
        if field in ("expression",):
            return find_math_expression(text) or text
        if field in ("item", "text", "content", "path", "title"):
            return text
        if field in ("id",):
            digits = "".join(ch for ch in text if ch.isdigit())
            return digits or None
        return text

    # ------------------------------------------------------------------ 自纠
    def _self_repair(self, ctx: PlanContext, segments: list[str], debug: dict) -> PlanDecision | None:
        """针对少数可预期的失败做一次参数修正重试。"""
        if ctx.step >= 3:
            return None

        tool = ctx.last_tool
        last = ctx.observations[-1]
        error = last.error or ""
        index = len(ctx.observations) - 1
        if index < 0 or index >= len(segments):
            return None
        segment_text = segments[index]
        spec = ctx.spec_of(tool) if tool else None
        if spec is None:
            return None

        retry_args: dict | None = None

        # 日期格式不对 → 去掉截止日期再试一次
        if tool == "todo" and ("日期" in error or "格式" in error):
            args, _ = extract("todo", segment_text, ctx)
            args.pop("due", None)
            retry_args = args
            debug["repair"] = "去掉无法解析的截止日期后重试"

        # 画图失败但手上还有数据 → 重新取一次数据再画
        if tool == "make_chart":
            args, missing = extract("make_chart", segment_text, ctx)
            if not missing:
                retry_args = args
                debug["repair"] = "重新从已有数据取标签与数值后重试"

        if retry_args is None:
            return None

        ok, fixed, issues = validate(spec.input_schema, retry_args)
        if not ok:
            return None
        return PlanDecision(
            thought=f"换个参数再试一次「{spec.title}」。",
            calls=[ToolCall(name=tool, arguments=fixed)],  # type: ignore[arg-type]
            debug=debug,
        )

    # ------------------------------------------------------------------ 闲聊
    def _smalltalk(self, text: str, ctx: PlanContext) -> str:
        lowered = text.lower()
        if any(word in lowered for word in _HELP_WORDS):
            titles = [(spec.title, spec.description.split("。")[0]) for spec in ctx.tools]
            return persona.help_text(titles)
        if any(word in lowered for word in _THANKS_WORDS):
            return persona.thanks(text)
        if any(word in lowered for word in _GREET_WORDS) or len(text) <= 4:
            return persona.greet(text)
        return persona.chat(text)

    @staticmethod
    def _clarify(scored: list[ScoredIntent], ctx: PlanContext) -> str:
        """分数处于灰区时，把候选工具摆出来让用户挑。"""
        options = []
        for item in scored[:3]:
            if item.score < THRESHOLD_CLARIFY:
                continue
            spec = ctx.spec_of(item.name)
            options.append(spec.title if spec else item.name)
        if not options:
            return "本龙没太听明白，你能再说具体一点吗？"
        return "你是想让我" + "、还是".join(options) + "呀？说清楚一点点，本龙马上办！"


def _main() -> int:
    """命令行调试入口：看规划器到底怎么想的。

        python -m agent.planner.mock_planner "帮我查上海天气然后画成柱状图"
    """
    import argparse
    import json

    from agent.registry import get_registry

    parser = argparse.ArgumentParser(description="规则式规划器调试")
    parser.add_argument("text", nargs="+", help="用户说的话")
    parser.add_argument("--channel", choices=["mcp", "builtin"], default="builtin")
    options = parser.parse_args()

    text = " ".join(options.text)
    ctx = PlanContext(user_text=text, tools=get_registry().all())
    decision = MockPlanner().plan(ctx)

    print(f"输入      : {text}")
    print(f"归一化后  : {normalize(text)}")
    print(f"切分片段  : {decision.debug.get('segments')}")
    print("打分明细  :")
    for segment_text, lines in decision.debug.get("scores", {}).items():
        print(f"  [{segment_text}]")
        for line in lines:
            print(f"    {line}")
    if decision.debug.get("slots"):
        print("槽位      :", json.dumps(decision.debug["slots"]["fixed"], ensure_ascii=False))
        for issue in decision.debug["slots"]["issues"]:
            print("  修正    :", issue)
    print(f"思考      : {decision.thought}")
    for call in decision.calls:
        print(f"动作      : {call.name}({json.dumps(call.arguments, ensure_ascii=False)})")
    if decision.ask_user:
        print(f"追问      : {decision.ask_user}")
    if decision.final_text:
        print(f"回答      : {decision.final_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
