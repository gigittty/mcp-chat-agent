"""文本归一化与多步切分。

不做分词
--------
引入 jieba 会增加一个模型依赖，而中文短指令的场景下
「子串命中 + 正则」已经足够，而且**完全可解释**——
我能说清每一分是怎么来的，这对课程作业里的「可解释性」很重要。

归一化处理的四类噪音
-------------------
1. 全角字符：``ＡＢＣ１２３``、``，。？！``
2. 中文数字：``三天`` → ``3天``、``两公里`` → ``2公里``
3. 句首礼貌用语：``帮我``/``麻烦你``/``能不能``
4. 多余空白与重复标点
"""

from __future__ import annotations

import re

from agent.planner.lexicon import CONNECTORS, POLITE_PREFIXES

# 全角 ASCII（！到 ～）与全角空格
_FULLWIDTH_MAP = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
_FULLWIDTH_MAP[0x3000] = 0x20

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

# 只有后面跟着量词时才替换，避免「算一下」被改成「算1下」
_CN_NUM_RE = re.compile(r"[零〇一二两三四五六七八九十百千]+(?=[天个次位张元分秒点号月年人杯碗遍公])")

_WHITESPACE_RE = re.compile(r"\s+")
_REPEAT_PUNCT_RE = re.compile(r"([，。！？,.!?])\1+")

#: 切分后要剥掉的边缘标点
_EDGE_PUNCT = "，。！？、,.!?;；:： 　"


def cn_to_int(text: str) -> int | None:
    """把「二十三」「一百二十」这类中文数字转成整数，无法解析返回 None。"""
    if not text:
        return None
    total = 0
    section = 0
    digit: int | None = None
    for char in text:
        if char in _CN_DIGITS:
            digit = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            # 「十二」里的「十」前面没有数字，视为 1
            section += (1 if digit is None else digit) * unit
            digit = None
        else:
            return None
    if digit is not None:
        section += digit
    total += section
    return total if total > 0 else None


def _replace_cn_numbers(text: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        value = cn_to_int(match.group(0))
        return str(value) if value is not None else match.group(0)

    return _CN_NUM_RE.sub(_sub, text)


def _strip_polite(text: str) -> str:
    """去掉句首礼貌用语。只剥一次，避免「帮我帮我」这种病态输入死循环。"""
    cleaned = text.lstrip(_EDGE_PUNCT)
    for prefix in sorted(POLITE_PREFIXES, key=len, reverse=True):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix):].lstrip(_EDGE_PUNCT)
    return cleaned


def normalize(text: str) -> str:
    """归一化。保留语义，只去掉噪音。"""
    if not text:
        return ""
    result = str(text).translate(_FULLWIDTH_MAP)
    result = _replace_cn_numbers(result)
    result = _WHITESPACE_RE.sub(" ", result)
    result = _REPEAT_PUNCT_RE.sub(r"\1", result).strip()
    result = _strip_polite(result)
    # 英文统一小写，方便匹配 ascii / sqrt 这类关键词
    result = re.sub(r"[A-Za-z]+", lambda m: m.group(0).lower(), result)
    return result.strip(_EDGE_PUNCT)


def segment(text: str) -> list[str]:
    """按连词把一句话切成多步。

    引号/括号内的连词不切分（「画个梗图：先苦后甜」不该被「后」切碎）。
    只返回非空片段；单步输入返回只含自身的一元列表。
    """
    if not text:
        return []

    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    index = 0
    length = len(text)
    connectors = sorted(CONNECTORS, key=len, reverse=True)

    while index < length:
        char = text[index]
        if char in "([{（【「《":
            depth += 1
        elif char in ")]}）】」》":
            depth = max(0, depth - 1)

        if depth == 0:
            matched = next((c for c in connectors if text.startswith(c, index)), None)
            if matched is not None:
                chunk = "".join(buffer).strip(_EDGE_PUNCT)
                if chunk:
                    parts.append(chunk)
                buffer = []
                index += len(matched)
                continue

        buffer.append(char)
        index += 1

    tail = "".join(buffer).strip(_EDGE_PUNCT)
    if tail:
        parts.append(tail)
    return parts or [text.strip(_EDGE_PUNCT)]


def extract_quoted(text: str) -> str | None:
    """取出引号内的内容（中文/英文引号都支持）。"""
    match = re.search(r"[「『\"'“”‘’]([^」』\"'“”‘’]{1,120})[」』\"'“”‘’]", text)
    return match.group(1).strip() if match else None


def strip_quotes(text: str) -> str:
    return text.strip("「」『』\"'“”‘’ ")
