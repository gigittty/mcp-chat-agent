"""槽位抽取：把自然语言里的参数抠出来。

每个工具一个抽取函数，返回 ``(参数 dict, 缺失的必填项列表)``。

关键设计
-------
- **别名归一**：说「魔都」要能查到上海的天气
- **跨步引用**：说「然后画成柱状图」时，图表的数据来自上一步的天气结果，
  这一步由 :func:`_series_from_last` 完成，不需要用户重复说数据
- **缺什么问什么**：缺必填槽位时不猜，交给规划器反问（多轮对话的基础）
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from agent.planner.base import PlanContext
from agent.planner.lexicon import (
    CHART_KINDS,
    CITY_ALIASES,
    CITY_NAMES,
    DAY_WORDS,
    MEME_STYLES,
)
from agent.planner.normalize import extract_quoted, strip_quotes

_CITY_PATTERN = re.compile(
    r"([\u4e00-\u9fa5]{2,6}?)(?:市|的)?(?:天气|气温|温度|下雨|多少度)"
)
_FILE_PATTERN = re.compile(r"([\w\u4e00-\u9fa5-]+\.(?:md|txt|json|csv|log|ya?ml))")
_FILE_NAME_HINT = re.compile(r"(?:名字叫|叫做|叫|命名为)\s*([\w\u4e00-\u9fa5.-]{1,60})")
_PRECISION = re.compile(r"保留\s*(\d{1,2})\s*位")
_DAYS = re.compile(r"(?:未来|最近|接下来)\s*(\d{1,2})\s*天")
_ID = re.compile(r"#\s*(\d{1,4})|第\s*(\d{1,4})\s*[条个]")
_SIDES = re.compile(r"(\d{1,3})\s*面")
_NUMBER_PAIR = re.compile(r"([\u4e00-\u9fa5A-Za-z]{1,8})\s*[:：]?\s*(-?\d+(?:\.\d+)?)")

_WEEKDAYS = {"周一": 0, "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6, "周天": 6}
_CN_NUM = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}

_LIST_WORDS = ("列出", "列一下", "看看", "有哪些", "清单", "待办", "列表", "查询")
_DONE_WORDS = ("做完", "已完成", "完成", "搞定", "打勾")
_REMOVE_WORDS = ("删掉", "删除", "去掉", "移除")
_ADD_WORDS = ("记一下", "记下来", "帮我记", "提醒", "添加", "加一个", "记着", "记上")


def _norm_city(raw: str) -> str:
    name = raw.strip()
    for prefix in ("的", "市"):
        name = name.removesuffix(prefix)
    name = name.rstrip("市")
    return CITY_ALIASES.get(name, name)


def find_city(text: str, prefs: dict | None = None) -> str | None:
    """从文本里找城市，支持别名与「XX的天气」句式。"""
    # 先精确匹配已知城市（长名优先，避免「吉林市」被切成「吉林」+「市」）
    for name in sorted(CITY_NAMES, key=len, reverse=True):
        if name in text:
            return name
    for alias, real in CITY_ALIASES.items():
        if alias in text:
            return real
    match = _CITY_PATTERN.search(text)
    if match:
        candidate = _norm_city(match.group(1))
        if candidate and candidate not in ("今天", "明天", "后天", "现在", "这里", "那边", "这边"):
            return candidate
    if prefs:
        saved = prefs.get("city")
        if saved:
            return str(saved)
    return None


def find_days(text: str) -> int:
    match = _DAYS.search(text)
    if match:
        return max(1, min(int(match.group(1)), 3))
    for word, value in DAY_WORDS.items():
        if word in text:
            return min(value, 3)
    return 1


def find_math_expression(text: str) -> str | None:
    """找出算式片段。

    「计算 3 的平方」这类不含运算符的中文说法，先翻译成符号再走通用逻辑。
    注意「平方根」必须排在「平方」前面判断，否则会被当成平方。
    """
    for pattern, template in (
        (r"(\d+(?:\.\d+)?)\s*(?:的)?(?:平方根|开平方|开根号|开方)", "sqrt({})"),
        (r"(\d+(?:\.\d+)?)\s*(?:的)?(?:平方|2次方)", "{}**2"),
        (r"(\d+(?:\.\d+)?)\s*(?:的)?(?:立方|3次方)", "{}**3"),
    ):
        found = re.search(pattern, text)
        if found:
            return template.format(found.group(1))

    candidates = re.findall(r"[0-9a-z+\-*/^%().\s]{3,}", text)
    best = ""
    for candidate in candidates:
        cleaned = candidate.strip(" .")
        if any(ch.isdigit() for ch in cleaned) and len(cleaned) > len(best):
            best = cleaned
    if not best:
        return None
    # 只有数字没有运算符时，问「1+1等于几」才是算式；单独一个数字没有意义
    if not re.search(r"[+\-*/^%()a-z]", best):
        return None
    return best.strip()


def find_precision(text: str) -> int:
    match = _PRECISION.search(text)
    if match:
        return max(0, min(int(match.group(1)), 12))
    return 4


def _series_from_last(ctx: PlanContext) -> dict | None:
    """从历史结果里往回找可画图的数据（跨步引用的核心）。

    倒序扫描而不是只看最后一条：如果上一步是「画图失败」，前面那条
    天气结果仍然应该被复用，这样自纠重试才有数据可用。
    """
    for observation in reversed(ctx.observations):
        if not observation.ok:
            continue
        series = observation.data.get("series")
        if isinstance(series, dict) and series.get("temp_max"):
            return series
        if isinstance(observation.data.get("values"), list) and observation.data["values"]:
            return {
                "date": [str(item) for item in observation.data.get("labels", [])],
                "temp_max": observation.data["values"],
            }
    # 再退一步看上一轮的结果（跨轮引用）
    if ctx.previous is not None and ctx.previous.ok:
        series = ctx.previous.data.get("series")
        if isinstance(series, dict) and series.get("temp_max"):
            return series
    return None


def _todo_due(text: str) -> str:
    match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
    if match:
        return match.group(1)
    today = date.today()
    if "今天" in text:
        return today.isoformat()
    if "明天" in text:
        return (today + timedelta(days=1)).isoformat()
    if "后天" in text:
        return (today + timedelta(days=2)).isoformat()
    for word, weekday in _WEEKDAYS.items():
        if word in text:
            delta = (weekday - today.weekday()) % 7
            return (today + timedelta(days=delta)).isoformat()
    return ""


def _todo_id(text: str) -> int | None:
    match = _ID.search(text)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _options(text: str) -> list[str]:
    """从「A 还是 B」「A、B、C」里抽出候选项。

    注意：归一化会把全角逗号变成半角，这里两种逗号都要切。
    """
    for splitter in ("还是", "或者", "、", ",", "/"):
        if splitter in text:
            parts = [strip_quotes(p.strip(" ？?")) for p in text.split(splitter)]
            parts = [p for p in parts if p and len(p) <= 20]
            if len(parts) >= 2:
                return parts
    return []


def _city_from_last(ctx: PlanContext) -> str:
    """从历史结果里找城市名，让图表标题更具体（「上海气温趋势」）。"""
    for observation in reversed(ctx.observations):
        if observation.ok and observation.data.get("city"):
            return str(observation.data["city"])
    if ctx.previous is not None and ctx.previous.ok and ctx.previous.data.get("city"):
        return str(ctx.previous.data["city"])
    return str(ctx.prefs.get("city") or "")


def _last_text(ctx: PlanContext) -> str | None:
    """取「刚才的结果」的文本，供「存成文件」用。"""
    for observation in reversed(ctx.observations):
        if not observation.ok:
            continue
        content = observation.data.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if observation.text:
            return observation.text
    if ctx.previous is not None and ctx.previous.ok:
        content = ctx.previous.data.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if ctx.previous.text:
            return ctx.previous.text
    return None


# ---------------------------------------------------------------- 各工具
def slots_calculate(text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    args: dict = {"precision": find_precision(text)}
    expression = find_math_expression(text)
    missing: list[str] = []
    if expression:
        args["expression"] = expression
    else:
        missing.append("expression")
    return args, missing


def slots_get_weather(text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    args: dict = {"days": find_days(text), "unit": "f" if "华氏" in text else "c"}
    missing: list[str] = []
    city = find_city(text, ctx.prefs)
    if city:
        args["city"] = city
    else:
        missing.append("city")
    return args, missing


def slots_todo(text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    missing: list[str] = []
    args: dict = {}

    if any(word in text for word in _REMOVE_WORDS):
        args["action"] = "remove"
    elif any(word in text for word in _DONE_WORDS):
        args["action"] = "done"
    elif any(word in text for word in _ADD_WORDS):
        args["action"] = "add"
    elif any(word in text for word in _LIST_WORDS):
        args["action"] = "list"
    else:
        args["action"] = "list" if "?" in text or "？" in text else "add"

    if args["action"] in ("done", "remove"):
        item_id = _todo_id(text)
        if item_id is None:
            missing.append("id")
        else:
            args["id"] = item_id

    if args["action"] == "add":
        item = None
        quoted = extract_quoted(text)
        if quoted:
            item = quoted
        else:
            body = re.sub(r"^(?:" + "|".join(map(re.escape, _ADD_WORDS)) + r")", "", text)
            item = body.strip(" :：,，")
        if item:
            args["item"] = item[:200]
            due = _todo_due(text)
            if due:
                args["due"] = due
        else:
            missing.append("item")

    return args, missing


def slots_make_chart(text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    args: dict = {}
    missing: list[str] = []

    kind = next((value for word, value in CHART_KINDS.items() if word in text), None)
    args["kind"] = kind or str(ctx.prefs.get("chart_kind") or "bar")

    quoted = extract_quoted(text)
    if quoted:
        args["title"] = quoted

    series = _series_from_last(ctx)
    if series:
        if not args.get("title"):
            city = _city_from_last(ctx)
            args["title"] = f"{city}气温趋势" if city else "气温趋势"
        args["labels"] = [str(item) for item in series["date"]]
        args["values"] = [_as_float(item) for item in series["temp_max"]]
        args["ylabel"] = "摄氏度"
        return args, missing

    pairs = _NUMBER_PAIR.findall(text)
    if len(pairs) >= 2:
        args["labels"] = [label for label, _ in pairs]
        args["values"] = [float(value) for _, value in pairs]
        return args, missing

    missing.extend(["labels", "values"])
    return args, missing


def slots_make_meme(text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    missing: list[str] = []
    args: dict = {}
    args["style"] = next((value for word, value in MEME_STYLES.items() if word in text), "impact")

    quoted = extract_quoted(text)
    if quoted:
        body = quoted
    else:
        # 「画个梗图：xxx」里的「画个」也要一起剥掉，所以量词用 * 而不是单次匹配
        body = re.sub(
            r"(?:帮我|给我|做|画|生成|来|一个|一张|个|张|的)*(?:梗图|表情包|海报|图片)",
            "",
            text,
        )
        body = body.strip(" :：,，。")
    args["text"] = body or text
    if not args["text"]:
        missing.append("text")

    # 归一化已把全角逗号转成半角，两种都判
    for separator in (",", "，"):
        if separator in args["text"]:
            head, _, tail = args["text"].partition(separator)
            args["text"] = head.strip()
            if tail.strip():
                args["subtext"] = tail.strip()
            break
    return args, missing


def slots_make_ascii_art(text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    args: dict = {"scale": 1}
    quoted = extract_quoted(text)
    if quoted:
        args["text"] = quoted
        return args, []
    cleaned = re.sub(r"(字符画|艺术字|点阵|ascii|把|转成|做成|生成|画)", "", text, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" :：,，。的")
    if cleaned:
        args["text"] = cleaned
        return args, []
    return args, ["text"]


def slots_files(text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    missing: list[str] = []
    args: dict = {}

    if any(word in text for word in ("有哪些文件", "列文件", "文件列表", "看看有什么文件")):
        return {"action": "list"}, []
    if any(word in text for word in _REMOVE_WORDS):
        args["action"] = "delete"
    elif any(word in text for word in ("读取", "读一下", "打开", "看看内容", "念一下")):
        args["action"] = "read"
    elif "追加" in text:
        args["action"] = "append"
    else:
        args["action"] = "write"

    match = _FILE_PATTERN.search(text)
    if match:
        args["path"] = match.group(1)
    else:
        hint = _FILE_NAME_HINT.search(text)
        if hint:
            name = hint.group(1).strip(" .")
            args["path"] = name if "." in name else name + ".md"
        elif args["action"] != "list":
            missing.append("path")

    if args["action"] in ("write", "append"):
        content = None
        quoted = extract_quoted(text)
        reference_words = ("刚才", "刚刚", "上面", "上一步", "这个结果", "这些结果", "它的")
        if quoted:
            content = quoted
        elif any(word in text for word in reference_words):
            content = _last_text(ctx)
        else:
            # 「存成 weather.md」这种没说存什么内容的，默认存上一次的结果；
            # 判断依据是：把动作词和文件名都剥掉之后，没剩下别的东西。
            residue = text
            residue = _FILE_PATTERN.sub("", residue)
            residue = _FILE_NAME_HINT.sub("", residue)
            for verb in ("保存", "存成", "存到", "写入", "写到", "追加到", "追加", "写进", "存进"):
                residue = residue.replace(verb, "")
            residue = strip_quotes(residue.strip(" :：,，。把将的"))
            if len(residue) <= 2:
                content = _last_text(ctx)
        if content:
            args["content"] = content
        else:
            missing.append("content")

    return args, missing


def slots_fun_pick(text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    args: dict = {}
    if any(word in text for word in ("骰子", "掷")):
        args["mode"] = "dice"
        sides = _SIDES.search(text)
        if sides:
            args["sides"] = max(2, min(int(sides.group(1)), 100))
    elif "硬币" in text:
        args["mode"] = "coin"
    elif any(word in text for word in ("吃什么", "午饭", "中午", "晚饭", "早餐", "夜宵")):
        args["mode"] = "lunch"
    elif any(word in text for word in ("还是", "选一个", "抽一个", "转盘", "哪个好", "决定")):
        args["mode"] = "wheel"
    elif "随机" in text:
        args["mode"] = "wheel"

    # 只有「从候选里挑一个」才需要 options；吃什么由工具内置菜单决定
    if args.get("mode") == "wheel":
        options = _options(text)
        cleaned: list[str] = []
        for item in options:
            candidate = re.sub(r"^(?:帮我|给我|还是|或者|选|抽|要)\s*", "", item)
            # 「火锅还是烧烤，你帮我选」→ 第二段要去掉尾部的「你帮我选」
            candidate = re.sub(
                r"(?:你|我)?(?:帮我|给我)?(?:选|挑|抽)(?:一个|哪个|一个吧)?(?:吧)?$", "", candidate
            )
            candidate = candidate.strip(" ,，。?？的")
            if candidate:
                cleaned.append(candidate)
        if len(cleaned) >= 2:
            args["options"] = cleaned[:10]
    return args, ([] if args.get("mode") else ["mode"])


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


EXTRACTORS = {
    "calculate": slots_calculate,
    "get_weather": slots_get_weather,
    "todo": slots_todo,
    "make_chart": slots_make_chart,
    "make_meme": slots_make_meme,
    "make_ascii_art": slots_make_ascii_art,
    "files": slots_files,
    "fun_pick": slots_fun_pick,
}


def extract(name: str, text: str, ctx: PlanContext) -> tuple[dict, list[str]]:
    """按工具名分派到对应抽取器。未知工具返回空参数。"""
    extractor = EXTRACTORS.get(name)
    if extractor is None:
        return {}, []
    return extractor(text, ctx)
