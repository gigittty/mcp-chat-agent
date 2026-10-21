"""计算器的安全性与正确性测试。

安全用例是硬门槛：任何一个表达式被成功求值，都说明白名单有漏洞。
"""

from __future__ import annotations

import pytest

from agent.tools import calc


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("1+1", 2),
        ("(12+8)*3/5", 12.0),
        ("2**10", 1024),
        ("sqrt(16)", 4.0),
        ("abs(-3.5)", 3.5),
        ("round(3.14159, 2)", 3.14),
        ("7 // 2", 3),
        ("7 % 3", 1),
        ("max(1, 5, 3)", 5),
        ("pi", 3.141592653589793),
        ("-5 + 2", -3),
        ("factorial(5)", 120),
    ],
)
def test_正常计算(expr: str, expected: float) -> None:
    result = calc.run({"expression": expr})
    assert result.ok, result.error
    assert result.data["value"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr",
    [
        "12*3+4",
        "12×3+4",       # 全角乘号
        "100÷4",        # 全角除号
        "（1+2）×3",     # 全角括号
        "2^10",         # ^ 当幂用
    ],
)
def test_兼容全角与常见符号(expr: str) -> None:
    assert calc.run({"expression": expr}).ok


# ---------------- 安全用例：必须全部被拒绝 ----------------
@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo hacked')",
        "().__class__.__bases__[0].__subclasses__()",
        "open('C:/Windows/win.ini').read()",
        "eval('1+1')",
        "exec('x=1')",
        "lambda: 1",
        "[x for x in range(10)]",
        "getattr(1, 'real')",
        "globals()",
        "[].__len__()",
        "1 if 1 else 2",            # IfExp
        "a + 1",                    # 未定义变量
        "1 < 2",                    # 比较运算
        "'abc' * 3",                # 字符串常量
        "True + 1",                 # bool 常量
        "x := 1",                   # 海象运算
        "import os",
        "1; 2",
    ],
)
def test_恶意或非法表达式被拒绝(expr: str) -> None:
    result = calc.run({"expression": expr})
    assert result.ok is False, f"不该被放行：{expr}"
    assert result.error
    assert result.hint


@pytest.mark.parametrize(
    "expr",
    [
        "2**999999999",      # 指数超限，会算很久
        "9999999**64",       # 底数超限
        "10**64 * 10**64",   # 结果量级过大
        "1/0",
        "0/0",
        "sqrt(-1)",
    ],
)
def test_资源与数值异常被拦下(expr: str) -> None:
    result = calc.run({"expression": expr})
    assert result.ok is False


def test_超长表达式被拒绝() -> None:
    expr = "1+" * 200 + "1"
    result = calc.run({"expression": expr})
    assert result.ok is False
    assert "太长" in (result.error or "")


def test_空表达式() -> None:
    for args in ({}, {"expression": ""}, {"expression": "   "}):
        result = calc.run(args)
        assert result.ok is False


def test_精度参数生效() -> None:
    result = calc.run({"expression": "10/3", "precision": 2})
    assert result.ok
    assert result.data["display"] == "3.33"


def test_整数结果不带小数点() -> None:
    result = calc.run({"expression": "2+2"})
    assert result.data["display"] == "4"


def test_结果可读文本() -> None:
    result = calc.run({"expression": "12*3"})
    assert "36" in result.text
