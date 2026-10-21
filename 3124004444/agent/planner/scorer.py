"""意图打分。

公式（每一项都会写进 ``breakdown``，所以结果完全可解释）::

    score = Σ trigger_w × min(hits, 2) × pos_boost(1.0~1.3)
          + Σ object_w  × min(hits, 2)
          + Σ negative_w
          + ctx_bonus            # 上一步的工具在当前规则的 followups 里
          + diversity_bonus      # 命中来源 ≥2 类

为什么要「位置权重」
------------------
「天气」出现在句首通常就是主题，出现在句尾可能是补充说明。
位置越靠前，boost 越大（最早命中给 1.3，最晚给 1.0）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.planner.base import PlanContext
from agent.planner.lexicon import INTENTS, IntentRule

#: 低于这个分不执行
THRESHOLD_ACT = 2.6
#: 介于两者之间 → 反问澄清而不是瞎猜
THRESHOLD_CLARIFY = 1.2
#: 上下文延续奖励
CTX_BONUS = 2.0
#: 命中来源多样性的小奖励
DIVERSITY_BONUS = 0.6


@dataclass
class ScoredIntent:
    name: str
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    matched: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{key}={value:+.2f}" for key, value in self.breakdown.items() if abs(value) > 1e-9]
        return f"{self.name}: {self.score:.2f} ({', '.join(parts) or '无命中'})"


def _position_boost(text: str, keyword: str) -> float:
    index = text.find(keyword)
    if index < 0 or not text:
        return 1.0
    ratio = index / max(len(text), 1)
    return 1.3 - 0.3 * ratio


def _score_rule(rule: IntentRule, text: str, ctx: PlanContext) -> ScoredIntent:
    result = ScoredIntent(name=rule.name)
    sources = 0

    trigger_points = 0.0
    for keyword, weight in rule.triggers.items():
        hits = text.count(keyword)
        if hits:
            points = weight * min(hits, 2) * _position_boost(text, keyword)
            trigger_points += points
            result.matched.append(keyword)
    if trigger_points:
        sources += 1
        result.breakdown["trigger"] = trigger_points

    object_points = 0.0
    for pattern, weight in rule.objects:
        try:
            hits = len(re.findall(pattern, text))
        except re.error:  # 规则写错不应该让整轮对话崩掉
            continue
        if hits:
            object_points += weight * min(hits, 2)
            result.matched.append(f"/{pattern}/")
    if object_points:
        sources += 1
        result.breakdown["object"] = object_points

    negative_points = 0.0
    for keyword, weight in rule.negatives:
        if keyword in text:
            negative_points += weight
            result.matched.append(f"-{keyword}")
    if negative_points:
        result.breakdown["negative"] = negative_points

    if ctx.last_tool and ctx.last_tool in rule.followups:
        result.breakdown["context"] = CTX_BONUS

    if sources >= 2:
        result.breakdown["diversity"] = DIVERSITY_BONUS

    result.score = sum(result.breakdown.values())
    return result


def score_text(text: str, ctx: PlanContext, candidates: list[str] | None = None) -> list[ScoredIntent]:
    """给所有（或指定的）意图打分，按分数降序返回。"""
    allowed = set(candidates) if candidates else set(INTENTS)
    scored = [
        _score_rule(rule, text, ctx)
        for name, rule in INTENTS.items()
        if name in allowed
    ]
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


def debug_table(scored: list[ScoredIntent], limit: int = 4) -> str:
    """给 --debug-planner 用的可读表格。"""
    lines = []
    for item in scored[:limit]:
        lines.append("  " + item.summary())
    return "\n".join(lines) or "  （没有任何意图命中）"
