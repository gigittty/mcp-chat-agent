"""生成图表（柱状图 / 折线图 / 饼图）。

中文字体是最容易翻车的地方：matplotlib 默认字体没有汉字，标题会变成方块。
这里统一通过 :mod:`agent.tools.fonts` 解析可用字体，并在 import pyplot
之前设置 Agg 后端（否则无显示环境会报错）。

「查天气 → 画柱状图」这类多步任务，就靠这个工具把上一步的数据变成图片。
"""

from __future__ import annotations

import threading

from agent.contracts import Artifact, ToolResult, ToolSpec
from agent.sandbox import output_path
from agent.tools.fonts import matplotlib_font_family, require_matplotlib

#: matplotlib 的 Figure 不是线程安全的，多个请求并发画图必须串行化
_PLOT_LOCK = threading.Lock()

_PALETTES = {
    "bar": ["#4F8DFD", "#5AC8FA", "#34C759", "#FF9F0A", "#FF6B6B", "#BF5AF2"],
    "line": ["#4F8DFD", "#FF6B6B", "#34C759"],
    "pie": ["#4F8DFD", "#5AC8FA", "#34C759", "#FF9F0A", "#FF6B6B", "#BF5AF2", "#8E8E93"],
}

SPEC = ToolSpec(
    name="make_chart",
    title="生成图表",
    description=(
        "把一组数据画成图表并保存为 PNG 图片。支持柱状图（bar）、折线图（line）、"
        "饼图（pie）。labels 与 values 两个数组长度必须一致。"
        "用户要求把数据可视化、画图、做个统计图时使用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "图表标题"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "横轴标签（饼图则为各扇区名称）",
            },
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "与 labels 一一对应的数值",
            },
            "kind": {
                "type": "string",
                "enum": ["bar", "line", "pie"],
                "default": "bar",
                "description": "图表类型",
            },
            "ylabel": {"type": "string", "description": "纵轴单位说明，可省略"},
        },
        "required": ["labels", "values"],
    },
    tags=["image", "chart"],
    produces=["chart.path"],
    examples=[{"title": "未来三天最高温", "labels": ["10-21", "10-22", "10-23"], "values": [21, 22, 23], "kind": "bar"}],
)


def _prepare(args: dict) -> tuple[str, list[str], list[float], str, str] | ToolResult:
    labels_raw = args.get("labels")
    values_raw = args.get("values")

    if not isinstance(labels_raw, list) or not labels_raw:
        return ToolResult.failure("缺少 labels（横轴标签）", hint="请给出标签数组，例如 [\"周一\",\"周二\"]")
    if not isinstance(values_raw, list) or not values_raw:
        return ToolResult.failure("缺少 values（数值）", hint="请给出与标签对应的数值数组")

    labels = [str(item) for item in labels_raw]
    values: list[float] = []
    for item in values_raw:
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            return ToolResult.failure(
                f"有一个数值不是数字：{item!r}",
                hint="values 里只能放数字",
            )

    if len(labels) != len(values):
        # 截断而不是直接失败：多步引用时数组长度可能对不齐
        size = min(len(labels), len(values))
        labels = labels[:size]
        values = values[:size]

    if not labels:
        return ToolResult.failure("标签和数值都是空的")

    kind = str(args.get("kind", "bar") or "bar").strip().lower()
    if kind not in ("bar", "line", "pie"):
        kind = "bar"

    title = str(args.get("title", "") or "").strip() or "数据图表"
    ylabel = str(args.get("ylabel", "") or "").strip()
    return title, labels, values, kind, ylabel


def _shrink(labels: list[str], limit: int = 12) -> list[str]:
    """标签太多时截断并加省略号，避免横轴糊成一团。"""
    return [text if len(text) <= limit else text[: limit - 1] + "…" for text in labels]


def run(args: dict) -> ToolResult:
    prepared = _prepare(args)
    if isinstance(prepared, ToolResult):
        return prepared
    title, labels, values, kind, ylabel = prepared

    require_matplotlib()
    matplotlib_font_family()

    import matplotlib.pyplot as plt

    target = output_path("chart")

    with _PLOT_LOCK:
        figure = None
        try:
            figure, axes = plt.subplots(figsize=(8.2, 5.0), dpi=130)
            colors = _PALETTES[kind]
            short_labels = _shrink(labels)

            if kind == "bar":
                bars = axes.bar(short_labels, values, color=colors[: len(values)] or colors[0])
                axes.bar_label(bars, fmt="%g", padding=2, fontsize=9)
                axes.grid(axis="y", linestyle="--", alpha=0.35)
                axes.set_axisbelow(True)
                if len(short_labels) > 6:
                    plt.setp(axes.get_xticklabels(), rotation=30, ha="right", fontsize=9)
            elif kind == "line":
                axes.plot(short_labels, values, marker="o", linewidth=2.2, color=colors[0])
                for x, y in zip(short_labels, values, strict=False):
                    axes.annotate(f"{y:g}", (x, y), textcoords="offset points", xytext=(0, 8),
                                  ha="center", fontsize=9)
                axes.grid(linestyle="--", alpha=0.35)
                axes.set_axisbelow(True)
            else:  # pie
                axes.pie(
                    values,
                    labels=short_labels,
                    autopct="%1.1f%%",
                    colors=colors[: len(values)] or None,
                    startangle=90,
                    textprops={"fontsize": 10},
                )
                axes.axis("equal")

            axes.set_title(title, fontsize=15, pad=14)
            if ylabel and kind != "pie":
                axes.set_ylabel(ylabel, fontsize=11)
            figure.tight_layout()
            figure.savefig(target, bbox_inches="tight")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(
                f"画图时出错了：{type(exc).__name__}: {exc}",
                hint="请检查 labels 与 values 是否成对，或换个图表类型",
            )
        finally:
            if figure is not None:
                plt.close(figure)      # 不关闭会累积句柄，几十张图后内存飙升

    kind_zh = {"bar": "柱状图", "line": "折线图", "pie": "饼图"}[kind]
    text = f"画好了{kind_zh}「{title}」，共 {len(values)} 组数据：{target.name}"
    return ToolResult.success(
        text,
        data={
            "path": str(target),
            "kind": kind,
            "labels": labels,
            "values": values,
            "n": len(values),
        },
        artifacts=[Artifact(kind="image", path=str(target), mime="image/png", caption=title)],
    )


if __name__ == "__main__":
    result = run(
        {
            "title": "未来三天最高气温",
            "labels": ["10-21", "10-22", "10-23"],
            "values": [21, 22, 23],
            "kind": "bar",
            "ylabel": "摄氏度",
        }
    )
    print(result.text)
