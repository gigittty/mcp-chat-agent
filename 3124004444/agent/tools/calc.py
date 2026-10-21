"""安全计算器。

为什么不用 ``eval()``
-------------------
``eval("__import__('os').system('rm -rf /')")`` 一行就能拿到系统权限。
即使加正则黑名单也会被各种编码技巧绕过。

这里改为**自己解释 AST**：先把表达式解析成语法树，然后只认一棵极小的
节点白名单（数字常量、四则运算、幂、取负、白名单里的数学函数），
遇到任何其他节点直接拒绝。整个过程从不调用 ``eval``/``exec``。

额外的资源保护
--------------
``2**999999999`` 这类表达式不长，但会让 CPython 算上几分钟。
所以幂运算单独限制底数与指数，并对最终结果做量级检查。
"""

from __future__ import annotations

import ast
import math

from agent.contracts import ToolResult, ToolSpec

MAX_EXPR_LEN = 200
MAX_DEPTH = 24
MAX_POW_EXPONENT = 64
MAX_POW_BASE = 1e6
MAX_RESULT_ABS = 1e100

_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}

_ALLOWED_UNARY = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}

#: 白名单函数。故意不含 open/eval/exec/getattr 之类。
_ALLOWED_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
}

_ALLOWED_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

#: 用户常输入的全角/数学符号 → Python 运算符
_CHAR_FIXES = {
    "×": "*", "✕": "*", "·": "*",
    "÷": "/", "／": "/",
    "＋": "+", "－": "-", "—": "-",
    "（": "(", "）": ")",
    "＾": "**", "^": "**",
    "：": ":",
    "　": " ",
    "％": "%",
}

SPEC = ToolSpec(
    name="calculate",
    title="计算器",
    description=(
        "计算数学表达式并返回数值结果。支持加减乘除、整除、取余、幂运算、"
        "括号，以及 sqrt/log/exp/abs/round/min/max/sin/cos/tan/floor/ceil/factorial "
        "等函数，常量 pi/e/tau。用户提出任何算术、百分比、开方、三角函数问题时使用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "要计算的表达式，例如 (12+8)*3/5 或 sqrt(16)+2**10",
            },
            "precision": {
                "type": "integer",
                "minimum": 0,
                "maximum": 12,
                "default": 4,
                "description": "结果保留的小数位数",
            },
        },
        "required": ["expression"],
    },
    tags=["calc"],
    produces=["calc.value"],
    examples=[{"expression": "(12 + 8) * 3 / 5", "precision": 2}],
)


class _UnsafeExpression(Exception):
    """表达式里出现了不允许的结构。"""


def _normalize(raw: str) -> str:
    text = str(raw)
    for src, dst in _CHAR_FIXES.items():
        text = text.replace(src, dst)
    return text.strip()


def _eval_node(node: ast.AST, depth: int) -> float:
    if depth > MAX_DEPTH:
        raise _UnsafeExpression("表达式嵌套太深")

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, depth + 1)

    if isinstance(node, ast.Constant):
        value = node.value
        # 注意 bool 是 int 的子类，必须显式排除
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _UnsafeExpression("只允许数字常量")
        return value

    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_CONSTS:
            return _ALLOWED_CONSTS[node.id]
        raise _UnsafeExpression(f"不允许的变量名：{node.id}")

    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_UNARY.get(type(node.op))
        if op is None:
            raise _UnsafeExpression("不支持的一元运算")
        return op(_eval_node(node.operand, depth + 1))

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, depth + 1)
        right = _eval_node(node.right, depth + 1)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_POW_EXPONENT:
                raise _UnsafeExpression(f"幂的指数太大（上限 {MAX_POW_EXPONENT}）")
            if abs(left) > MAX_POW_BASE:
                raise _UnsafeExpression(f"幂的底数太大（上限 {MAX_POW_BASE:g}）")
            try:
                return left**right
            except OverflowError as exc:
                raise _UnsafeExpression("幂运算结果溢出") from exc
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise _UnsafeExpression("不支持的运算符")
        try:
            return op(left, right)
        except ZeroDivisionError as exc:
            raise _UnsafeExpression("除数不能为 0") from exc
        except OverflowError as exc:
            raise _UnsafeExpression("运算结果溢出") from exc

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise _UnsafeExpression("只允许调用内置的数学函数")
        func = _ALLOWED_FUNCS.get(node.func.id)
        if func is None:
            raise _UnsafeExpression(f"不允许调用的函数：{node.func.id}")
        if node.keywords:
            raise _UnsafeExpression("函数不支持关键字参数")
        args = [_eval_node(arg, depth + 1) for arg in node.args]
        try:
            return func(*args)
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            raise _UnsafeExpression(f"{node.func.id}() 参数非法：{exc}") from exc

    if isinstance(node, ast.Compare):
        raise _UnsafeExpression("不支持比较运算，请直接给出算式")
    raise _UnsafeExpression(f"不支持的语法：{type(node).__name__}")


def safe_eval(expression: str) -> float:
    """安全求值。非法表达式抛 _UnsafeExpression。"""
    text = _normalize(expression)
    if not text:
        raise _UnsafeExpression("表达式为空")
    if len(text) > MAX_EXPR_LEN:
        raise _UnsafeExpression(f"表达式太长（上限 {MAX_EXPR_LEN} 字符）")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise _UnsafeExpression(f"表达式语法错误：{exc.msg}") from exc

    value = _eval_node(tree, 0)
    if isinstance(value, complex):
        raise _UnsafeExpression("结果是复数，暂不支持")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise _UnsafeExpression("结果不是有限数")
    if abs(value) > MAX_RESULT_ABS:
        raise _UnsafeExpression("结果太大，超出可表示范围")
    return value


def _fmt(value: float, precision: int) -> str:
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return str(int(value))
    return f"{value:.{precision}f}".rstrip("0").rstrip(".")


def run(args: dict) -> ToolResult:
    expression = str(args.get("expression", "")).strip()
    if not expression:
        return ToolResult.failure("没有给出要计算的表达式", hint="请告诉我要算什么，例如 (12+8)*3/5")

    try:
        precision = int(args.get("precision", 4))
    except (TypeError, ValueError):
        precision = 4
    precision = max(0, min(precision, 12))

    try:
        value = safe_eval(expression)
    except _UnsafeExpression as exc:
        return ToolResult.failure(
            f"算不了这个式子：{exc}",
            hint="只能做四则运算、幂、括号和白名单数学函数，请换个写法",
        )

    shown = _fmt(value, precision)
    normalized = _normalize(expression)
    text = f"{normalized} = {shown}" if normalized != str(value) else f"结果是 {shown}"
    return ToolResult.success(
        text,
        data={"value": value, "display": shown, "expression": normalized},
    )


if __name__ == "__main__":  # 便于单文件调试：python -m agent.tools.calc "sqrt(16)+2**10"
    import sys

    print(run({"expression": " ".join(sys.argv[1:]) or "1+1"}).text)
