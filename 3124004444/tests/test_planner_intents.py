"""规划器准确率门槛测试。

这是判断「规则式规划器是不是玩具」的关键：
表驱动 40+ 条真实中文说法（含口语、省略、错别字式表达、负例），
要求 **Top-1 工具选择准确率 ≥ 90%**、**参数抽取准确率 ≥ 85%**。

只要有人把词表改坏了，这个测试会立刻红。
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from agent.contracts import ToolResult
from agent.planner.base import PlanContext
from agent.planner.mock_planner import MockPlanner
from agent.registry import get_registry

MIN_INTENT_ACCURACY = 0.90
MIN_SLOT_ACCURACY = 0.85


@pytest.fixture(scope="module")
def tools():
    return get_registry().all()


@pytest.fixture(scope="module")
def planner():
    return MockPlanner()


def plan(planner, tools, text, **kwargs):
    return planner.plan(PlanContext(user_text=text, tools=tools, **kwargs))


def picked(decision) -> str | None:
    return decision.calls[0].name if decision.calls else None


def _contains(needle: str) -> Callable[[Any], bool]:
    return lambda actual: needle in str(actual)


#: (输入, 期望工具（None 表示不该调工具）, 需要满足的参数条件)
CASES: list[tuple[str, str | None, dict[str, Any]]] = [
    # ---------------- 计算 ----------------
    ("算一下 1+1", "calculate", {"expression": _contains("1+1")}),
    ("帮我计算 (12+8)*3/5", "calculate", {"expression": _contains("(12+8)*3/5")}),
    ("sqrt(16) 等于几", "calculate", {"expression": _contains("sqrt(16)")}),
    ("2**10 是多少", "calculate", {"expression": _contains("2**10")}),
    ("保留两位小数算 10/3", "calculate", {"precision": 2}),
    ("计算 3 的平方", "calculate", {}),
    ("100/4 等于多少", "calculate", {}),
    # ---------------- 天气 ----------------
    ("北京今天天气怎么样", "get_weather", {"city": "北京", "days": 1}),
    ("上海天气", "get_weather", {"city": "上海"}),
    ("查一下杭州的天气预报", "get_weather", {"city": "杭州"}),
    ("深圳未来三天天气", "get_weather", {"city": "深圳", "days": 3}),
    ("明天广州会下雨吗", "get_weather", {"city": "广州"}),
    ("魔都天气如何", "get_weather", {"city": "上海"}),
    ("杭州气温多少度", "get_weather", {"city": "杭州"}),
    ("成都冷不冷", "get_weather", {"city": "成都"}),
    ("厦门要带伞吗", "get_weather", {"city": "厦门"}),
    # ---------------- 待办 ----------------
    ("记一下：周四交作业", "todo", {"action": "add", "item": _contains("交作业")}),
    ("提醒我明天开会", "todo", {"action": "add"}),
    ("待办清单", "todo", {"action": "list"}),
    ("列出待办", "todo", {"action": "list"}),
    ("把 #1 标记完成", "todo", {"action": "done", "id": 1}),
    ("删掉 #2", "todo", {"action": "remove", "id": 2}),
    ("帮我记一下买牛奶", "todo", {"action": "add", "item": _contains("买牛奶")}),
    # ---------------- 图表 ----------------
    ("画个柱状图：苹果 3，香蕉 5", "make_chart", {"kind": "bar"}),
    ("用这些数据做个饼图：甲 3，乙 5", "make_chart", {"kind": "pie"}),
    ("生成折线图对比 一月 10 二月 20", "make_chart", {"kind": "line"}),
    # ---------------- 梗图 ----------------
    ("画个梗图：周五了", "make_meme", {"style": "impact", "text": "周五了"}),
    ("做个表情包「今天不想上班」", "make_meme", {"text": _contains("今天不想上班")}),
    ("做张海报：团子龙新品发布", "make_meme", {"style": "poster"}),
    ("来一张恐龙海报", "make_meme", {"style": "dragon"}),
    # ---------------- ASCII ----------------
    ("把 HI 转成字符画", "make_ascii_art", {"text": _contains("hi")}),
    ("生成 ascii 艺术字 HELLO", "make_ascii_art", {}),
    ("字符画 123", "make_ascii_art", {}),
    ("点阵图 ABC", "make_ascii_art", {}),
    # ---------------- 文件 ----------------
    ("把「你好」写成 a.txt", "files", {"action": "write", "path": "a.txt"}),
    ("读取 note.md", "files", {"action": "read", "path": "note.md"}),
    ("看看有哪些文件", "files", {"action": "list"}),
    ("删掉 old.txt", "files", {"action": "delete", "path": "old.txt"}),
    ("保存为 weekly.json", "files", {"action": "write", "path": "weekly.json"}),
    # ---------------- 趣味决策 ----------------
    ("掷骰子", "fun_pick", {"mode": "dice"}),
    ("抛硬币", "fun_pick", {"mode": "coin"}),
    ("今天吃什么", "fun_pick", {"mode": "lunch"}),
    ("火锅还是烧烤，你帮我选", "fun_pick", {"mode": "wheel"}),
    ("咖啡还是奶茶还是可乐", "fun_pick", {"mode": "wheel", "options": ["咖啡", "奶茶", "可乐"]}),
    ("帮我决定，火锅还是烧烤", "fun_pick", {"mode": "wheel"}),
    # ---------------- 负例：不该调任何工具 ----------------
    ("你好", None, {}),
    ("天气之子好看吗", None, {}),
    ("谢谢", None, {}),
    ("你能做什么", None, {}),
    ("画个饼", None, {}),
    ("随便聊聊吧", None, {}),
]


def test_意图识别准确率(planner, tools) -> None:
    wrong: list[str] = []
    for text, expected, _checks in CASES:
        decision = plan(planner, tools, text)
        actual = picked(decision)
        if actual != expected:
            wrong.append(f"  {text!r} → 期望 {expected}，实际 {actual}")

    accuracy = 1 - len(wrong) / len(CASES)
    assert accuracy >= MIN_INTENT_ACCURACY, (
        f"意图识别准确率 {accuracy:.1%} 低于门槛 {MIN_INTENT_ACCURACY:.0%}\n"
        + "\n".join(wrong)
    )


def test_参数抽取准确率(planner, tools) -> None:
    checks_total = 0
    checks_ok = 0
    wrong: list[str] = []

    for text, expected, checks in CASES:
        if not checks:
            continue
        decision = plan(planner, tools, text)
        if picked(decision) != expected:
            continue          # 选错工具的已在上面报过，这里不重复计
        arguments = decision.calls[0].arguments
        for key, want in checks.items():
            checks_total += 1
            # 沿用默认值的参数不会被放进 arguments，先补 schema 默认值再比
            actual = arguments.get(key)
            if actual is None:
                spec = next((s for s in tools if s.name == expected), None)
                if spec:
                    actual = spec.properties().get(key, {}).get("default")
            good = want(actual) if callable(want) else actual == want
            if good:
                checks_ok += 1
            else:
                wrong.append(f"  {text!r} → {key} 期望 {want}，实际 {actual!r}")

    assert checks_total > 0
    accuracy = checks_ok / checks_total
    assert accuracy >= MIN_SLOT_ACCURACY, (
        f"参数抽取准确率 {accuracy:.1%} 低于门槛 {MIN_SLOT_ACCURACY:.0%}\n"
        + "\n".join(wrong)
    )


# ---------------------------------------------------------------- 多步与多轮
def test_多步请求先做第一步(planner, tools) -> None:
    decision = plan(planner, tools, "帮我查上海未来三天天气，然后画成柱状图")
    assert picked(decision) == "get_weather"
    assert decision.calls[0].arguments["city"] == "上海"


def test_第二步复用第一步的数据(planner, tools) -> None:
    weather = ToolResult.success(
        "上海未来三天",
        data={
            "city": "上海",
            "series": {"date": ["10-21", "10-22", "10-23"], "temp_max": [21, 22, 23]},
        },
    )
    decision = plan(
        planner,
        tools,
        "帮我查上海未来三天天气，然后画成柱状图",
        observations=[weather],
        last_tool="get_weather",
    )
    assert picked(decision) == "make_chart"
    arguments = decision.calls[0].arguments
    assert arguments["labels"] == ["10-21", "10-22", "10-23"]
    assert arguments["values"] == [21.0, 22.0, 23.0]
    assert arguments["kind"] == "bar"


def test_三步都做完后输出总结(planner, tools) -> None:
    weather = ToolResult.success("天气查完了", data={"series": {"date": ["d1"], "temp_max": [1]}})
    chart = ToolResult.success("图画好了", data={"path": "x.png"})
    decision = plan(
        planner,
        tools,
        "帮我查上海天气，然后画成柱状图",
        observations=[weather, chart],
        last_tool="make_chart",
    )
    assert decision.final_text
    assert "天气查完了" in decision.final_text


def test_缺槽位时反问并记下待补(planner, tools) -> None:
    decision = plan(planner, tools, "天气怎么样")
    assert decision.ask_user
    assert "城市" in decision.ask_user
    assert decision.debug.get("pending", {}).get("name") == "get_weather"


def test_下一轮补上城市就能继续(planner, tools) -> None:
    pending = {"name": "get_weather", "args": {"days": 1, "unit": "c"}, "missing": ["city"]}
    decision = plan(planner, tools, "北京", pending=pending)
    assert picked(decision) == "get_weather"
    assert decision.calls[0].arguments["city"] == "北京"


def test_失败后去掉非法日期重试(planner, tools) -> None:
    failed = ToolResult.failure("截止日期格式不对：明天", hint="请用 2026-10-25 这样的格式")
    decision = plan(
        planner,
        tools,
        "记一下：交作业 明天",
        observations=[failed],
        last_tool="todo",
        step=1,
    )
    assert picked(decision) == "todo"
    assert "due" not in decision.calls[0].arguments


def test_失败且无法自纠时如实认错(planner, tools) -> None:
    failed = ToolResult.failure("完全没救了", hint="换个说法试试")
    decision = plan(
        planner,
        tools,
        "北京天气",
        observations=[failed],
        last_tool="get_weather",
        step=1,
    )
    assert decision.ask_user
    assert "换个说法" in decision.ask_user


def test_跨轮引用上一步结果(planner, tools) -> None:
    previous = ToolResult.success("北京 20℃", data={"content": "# 北京天气 20℃"})
    decision = plan(planner, tools, "存成 weather.md", previous=previous)
    assert picked(decision) == "files"
    assert "北京天气" in decision.calls[0].arguments["content"]


def test_调试信息包含打分明细(planner, tools) -> None:
    decision = plan(planner, tools, "北京今天天气怎么样")
    scores = decision.debug.get("scores", {})
    assert scores, "debug 里应有打分明细"
    joined = " ".join(next(iter(scores.values())))
    assert "trigger=" in joined
