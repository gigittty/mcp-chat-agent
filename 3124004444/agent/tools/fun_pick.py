"""趣味决策工具：掷骰子、抛硬币、今天吃什么、转盘。

这是「有趣」的落点——它不改变世界，只解决人类真正纠结的问题：
中午吃什么。纯标准库实现，永远不会因为网络或依赖失败。
"""

from __future__ import annotations

import random

from agent.contracts import ToolResult, ToolSpec

_LUNCH_POOL = [
    "兰州拉面", "黄焖鸡米饭", "麻辣烫", "砂锅粥", "饺子",
    "螺蛳粉", "烤鱼", "日式咖喱", "汉堡薯条", "煲仔饭",
    "酸菜鱼", "铁板炒饭", "三明治", "寿司", "火锅",
    "盖浇饭", "越南米粉", "炸鸡啤酒", "炒面", "小笼包",
]

SPEC = ToolSpec(
    name="fun_pick",
    title="趣味决策",
    description=(
        "做随机选择：掷骰子（dice）、抛硬币（coin）、随机挑一个午餐（lunch）、"
        "从给定选项里抽一个（wheel）。用户说「帮我决定」「掷个骰子」「今天吃什么」"
        "「随机选一个」时使用。wheel 模式需要提供 options。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["dice", "coin", "lunch", "wheel"],
                "description": "dice=掷骰子，coin=抛硬币，lunch=随机午餐，wheel=从选项里抽一个",
            },
            "sides": {
                "type": "integer",
                "minimum": 2,
                "maximum": 100,
                "default": 6,
                "description": "骰子面数（仅 dice 模式）",
            },
            "count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 1,
                "description": "掷几个骰子（仅 dice 模式）",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "候选选项（wheel 模式必填，lunch 模式可覆盖内置菜单）",
            },
        },
        "required": ["mode"],
    },
    tags=["fun"],
    produces=["fun_pick.choice"],
    examples=[{"mode": "dice", "sides": 6, "count": 2}],
)


def _clamp(value: object, low: int, high: int, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clean_options(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    cleaned: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def run(args: dict) -> ToolResult:
    mode = str(args.get("mode", "")).strip().lower()
    options = _clean_options(args.get("options"))
    rng = random.Random()          # 每次调用都用系统熵，避免连续几轮结果相同

    if mode == "dice":
        sides = _clamp(args.get("sides", 6), 2, 100, 6)
        count = _clamp(args.get("count", 1), 1, 10, 1)
        rolls = [rng.randint(1, sides) for _ in range(count)]
        total = sum(rolls)
        detail = " + ".join(str(r) for r in rolls)
        text = f"掷了 {count} 个 {sides} 面骰子：{detail}"
        if count > 1:
            text += f"，合计 {total} 点"
        if count == 1 and sides == 100:
            text += "（百分骰）"
        return ToolResult.success(text, data={"rolls": rolls, "total": total, "sides": sides, "mode": mode})

    if mode == "coin":
        choice = rng.choice(["正面", "反面"])
        return ToolResult.success(f"硬币结果：{choice}！", data={"choice": choice, "mode": mode})

    if mode == "lunch":
        pool = options or list(_LUNCH_POOL)
        choice = rng.choice(pool)
        return ToolResult.success(
            f"本龙替你决定了：今天吃「{choice}」！",
            data={"choice": choice, "pool_size": len(pool), "mode": mode},
        )

    if mode == "wheel":
        if len(options) < 2:
            return ToolResult.failure(
                "转盘至少需要两个选项",
                hint="请把候选选项列出来，例如：火锅 还是 烧烤",
            )
        choice = rng.choice(options)
        return ToolResult.success(
            f"转盘转完啦，结果是「{choice}」（从 {len(options)} 个选项里抽的）",
            data={"choice": choice, "options": options, "mode": mode},
        )

    return ToolResult.failure(
        f"不认识的模式：{mode or '（空）'}",
        hint="可用模式：dice / coin / lunch / wheel",
    )


if __name__ == "__main__":
    print(run({"mode": "lunch"}).text)
