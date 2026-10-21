"""JSON Schema 校验与参数修复。

为什么不直接用 jsonschema 库
--------------------------
我们只需要其中一个很小的子集（type/required/enum/range/pattern/items），
而校验失败时**还要能自动修复**——例如用户说「柱子图」应该纠正成 ``bar``，
``"3"`` 应该变成 ``3``，超出范围的 ``days=99`` 应该钳到 3。

纯校验库只会告诉你「不合法」，不会帮你改。所以这里自己写：
先尽量修，修不好才判失败，失败结果带 ``hint`` 交给规划器重新决策。
"""

from __future__ import annotations

import re
from typing import Any

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}

#: 常见口语 → 枚举值。让「柱子图」也能落进 bar。
_ENUM_SYNONYMS: dict[str, str] = {
    "柱子图": "bar", "柱形图": "bar", "条形": "bar", "柱": "bar",
    "折线": "line", "曲线": "line", "线图": "line", "走势图": "line",
    "圆饼图": "pie", "饼状": "pie", "扇形": "pie",
    "摄氏度": "c", "摄氏": "c", "华氏度": "f", "华氏": "f",
    "大字": "impact", "便条": "note", "宣传海报": "poster", "恐龙": "dragon",
    "写": "write", "存": "write", "读": "read", "追加写": "append",
    "骰": "dice", "钱币": "coin", "午饭": "lunch", "转": "wheel",
}


def _coerce(value: Any, expected: str) -> Any:
    """尝试把值转成期望类型。转不了就原样返回。"""
    if _TYPE_CHECKS.get(expected, lambda _v: True)(value):
        return value
    try:
        if expected == "integer":
            return int(str(value).strip())
        if expected == "number":
            return float(str(value).strip())
        if expected == "string":
            return str(value)
        if expected == "boolean":
            return str(value).strip().lower() in ("1", "true", "yes", "on", "y", "是")
        if expected == "array":
            if isinstance(value, str):
                return [part.strip() for part in re.split(r"[,，、;；]", value) if part.strip()]
            return [value]
    except (TypeError, ValueError):
        return value
    return value


def _match_enum(value: Any, options: list[Any]) -> Any:
    """把值对齐到枚举：先精确匹配，再同义词，再子串包含。"""
    text = str(value).strip()
    for option in options:
        if text == str(option):
            return option
    lowered = text.lower()
    for option in options:
        if lowered == str(option).lower():
            return option
    if text in _ENUM_SYNONYMS and _ENUM_SYNONYMS[text] in [str(o) for o in options]:
        return _ENUM_SYNONYMS[text]
    for option in options:
        option_text = str(option)
        if option_text and (option_text in text or text in option_text):
            return option
    return value


def validate(schema: dict, arguments: dict) -> tuple[bool, dict, list[str]]:
    """校验并修复参数。

    Returns:
        (是否通过, 修复后的参数, 问题描述列表)
    """
    if not isinstance(arguments, dict):
        return False, {}, ["参数必须是对象"]

    # 服务端不回传 None；显式过滤掉，让默认值生效
    cleaned = {key: value for key, value in arguments.items() if value is not None}
    issues: list[str] = []
    properties: dict[str, dict] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    # 1) 补默认值
    for name, prop in properties.items():
        if name not in cleaned and "default" in prop:
            cleaned[name] = prop["default"]

    # 2) 类型与约束
    for name, prop in properties.items():
        if name not in cleaned:
            continue
        expected = prop.get("type")
        if expected:
            coerced = _coerce(cleaned[name], expected)
            if coerced is not cleaned[name] and coerced != cleaned[name]:
                issues.append(f"{name}: 从 {cleaned[name]!r} 修正为 {coerced!r}")
            cleaned[name] = coerced
            if not _TYPE_CHECKS.get(expected, lambda _v: True)(coerced):
                issues.append(f"{name}: 需要 {expected} 类型，收到 {type(coerced).__name__}")
                continue

        enum = prop.get("enum")
        if isinstance(enum, list) and enum:
            aligned = _match_enum(cleaned[name], enum)
            if aligned != cleaned[name]:
                issues.append(f"{name}: {cleaned[name]!r} 对齐为 {aligned!r}")
                cleaned[name] = aligned
            if aligned not in enum:
                issues.append(f"{name}: 只能是 {enum} 之一")

        if isinstance(cleaned.get(name), (int, float)) and not isinstance(cleaned[name], bool):
            low, high = prop.get("minimum"), prop.get("maximum")
            if low is not None and cleaned[name] < low:
                issues.append(f"{name}: {cleaned[name]} 小于下限 {low}，已调整")
                cleaned[name] = low
            if high is not None and cleaned[name] > high:
                issues.append(f"{name}: {cleaned[name]} 大于上限 {high}，已调整")
                cleaned[name] = high

        if isinstance(cleaned.get(name), list):
            items_schema = prop.get("items") or {}
            item_type = items_schema.get("type")
            if item_type:
                cleaned[name] = [_coerce(item, item_type) for item in cleaned[name]]
            minimum = prop.get("minItems")
            if minimum is not None and len(cleaned[name]) < minimum:
                issues.append(f"{name}: 至少需要 {minimum} 项")

    # 3) 必填
    missing = [name for name in required if name not in cleaned or cleaned[name] in (None, "", [])]
    for name in missing:
        issues.append(f"缺少必填参数 {name}")

    failed = bool(missing) or any("只能是" in issue or "需要" in issue for issue in issues)
    return (not failed), cleaned, issues


def describe_issues(issues: list[str], limit: int = 3) -> str:
    return "；".join(issues[:limit])
