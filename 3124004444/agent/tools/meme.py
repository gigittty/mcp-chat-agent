"""生成梗图 / 海报。

全部本地绘制，不调用任何文生图 API，因此**零成本、断网也能出图**。

中文字体是这里的头号坑：
- ``ImageFont.truetype`` 传字体名在非默认目录会直接抛 OSError
- ``.ttc`` 是字体集合，传错 index 可能取到没有汉字的那个
- 找不到字体时如果不兜底，整个工具会崩

所以字体统一走 :mod:`agent.tools.fonts` 的探测链，并用
``draw.textlength`` 测量宽度做自动换行。

**关于吉祥物**：``style="dragon"`` 里的小恐龙是用几何图形逐块画出来的
原创图形，不使用任何第三方卡通形象的素材。
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from agent.contracts import Artifact, ToolResult, ToolSpec
from agent.sandbox import output_path
from agent.tools.fonts import pillow_font, require_pillow

WIDTH, HEIGHT = 900, 700

_PALETTES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "warm": ((255, 138, 101), (255, 82, 82)),
    "cool": ((79, 141, 253), (52, 71, 168)),
    "mono": ((60, 60, 60), (18, 18, 18)),
    "mint": ((110, 231, 183), (16, 150, 130)),
}

SPEC = ToolSpec(
    name="make_meme",
    title="生成梗图",
    description=(
        "根据文字生成一张梗图或海报 PNG。text 是主标题（会放大加粗），"
        "subtext 是副标题（可省略）。style 可选 impact（大字梗图）、note（便签）、"
        "poster（海报）、dragon（带一只小恐龙的海报）。"
        "用户要求做表情包、梗图、海报、配图时使用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "主标题文字"},
            "subtext": {"type": "string", "description": "副标题/落款，可省略"},
            "style": {
                "type": "string",
                "enum": ["impact", "note", "poster", "dragon"],
                "default": "impact",
                "description": "版式风格",
            },
            "palette": {
                "type": "string",
                "enum": ["warm", "cool", "mono", "mint"],
                "default": "warm",
                "description": "配色方案",
            },
        },
        "required": ["text"],
    },
    tags=["image", "meme"],
    produces=["meme.path"],
    examples=[{"text": "周五了", "subtext": "代码还没写完", "style": "impact", "palette": "warm"}],
)


def _gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """竖直渐变背景。逐行画线，900x700 大约几十毫秒。"""
    canvas = Image.new("RGB", (WIDTH, HEIGHT), top)
    draw = ImageDraw.Draw(canvas)
    for y in range(HEIGHT):
        ratio = y / max(HEIGHT - 1, 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (WIDTH, y)], fill=color)
    return canvas


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """按像素宽度自动换行。中英混排都能用——逐字符试宽，遇到空格优先断行。"""
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    center_y: int,
    *,
    fill: tuple[int, int, int] = (255, 255, 255),
    stroke: tuple[int, int, int] | None = (0, 0, 0),
    stroke_width: int = 6,
    max_width: int = WIDTH - 120,
    line_gap: int = 14,
) -> int:
    """居中绘制多行文字，返回整体高度。"""
    lines = _wrap(draw, text, font, max_width)
    line_height = font.size + line_gap
    total = line_height * len(lines)
    y = center_y - total // 2
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text(
            ((WIDTH - width) / 2, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width if stroke else 0,
            stroke_fill=stroke,
        )
        y += line_height
    return total


def _fit_font(text: str, max_size: int, min_size: int, max_width: int, bold: bool = True):
    """在给定宽度内挑一个尽量大的字号。"""
    measure = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    size = max_size
    while size > min_size:
        font = pillow_font(size, bold=bold)
        if measure.textlength(text, font=font) <= max_width:
            return font
        size -= 4
    return pillow_font(min_size, bold=bold)


def _draw_dragon(canvas: Image.Image, center: tuple[int, int], scale: float = 1.0) -> None:
    """画一只原创圆滚滚小恐龙。

    用椭圆/圆角矩形拼出来，没有任何第三方素材。
    """
    draw = ImageDraw.Draw(canvas, "RGBA")
    cx, cy = center

    def s(value: float) -> int:
        return int(value * scale)

    body = (88, 200, 140)
    body_dark = (52, 158, 106)
    belly = (214, 246, 224)
    pink = (255, 168, 184)
    dark = (40, 48, 46)

    # 尾巴
    draw.polygon(
        [
            (cx - s(120), cy + s(70)),
            (cx - s(196), cy + s(112)),
            (cx - s(118), cy + s(128)),
        ],
        fill=body_dark,
    )
    # 脚
    draw.ellipse(
        [cx - s(86), cy + s(96), cx - s(26), cy + s(140)], fill=body_dark
    )
    draw.ellipse(
        [cx + s(26), cy + s(96), cx + s(86), cy + s(140)], fill=body_dark
    )
    # 身体
    draw.ellipse([cx - s(112), cy - s(20), cx + s(112), cy + s(120)], fill=body)
    # 肚皮
    draw.ellipse([cx - s(66), cy + s(4), cx + s(66), cy + s(104)], fill=belly)
    # 头
    draw.ellipse([cx - s(88), cy - s(116), cx + s(88), cy + s(8)], fill=body)
    # 犄角
    draw.polygon(
        [(cx - s(52), cy - s(104)), (cx - s(34), cy - s(146)), (cx - s(16), cy - s(104))],
        fill=(255, 214, 102),
    )
    draw.polygon(
        [(cx + s(16), cy - s(104)), (cx + s(34), cy - s(146)), (cx + s(52), cy - s(104))],
        fill=(255, 214, 102),
    )
    # 眼睛
    for offset in (-36, 36):
        draw.ellipse(
            [cx + s(offset) - s(18), cy - s(76) - s(20), cx + s(offset) + s(18), cy - s(76) + s(20)],
            fill=(255, 255, 255),
        )
        draw.ellipse(
            [cx + s(offset) - s(9), cy - s(76) - s(11), cx + s(offset) + s(9), cy - s(76) + s(11)],
            fill=dark,
        )
        draw.ellipse(
            [cx + s(offset) - s(2), cy - s(76) - s(9), cx + s(offset) + s(5), cy - s(76) - s(2)],
            fill=(255, 255, 255),
        )
    # 腮红
    for offset in (-58, 58):
        draw.ellipse(
            [cx + s(offset) - s(15), cy - s(40) - s(9), cx + s(offset) + s(15), cy - s(40) + s(9)],
            fill=pink,
        )
    # 嘴
    draw.arc(
        [cx - s(22), cy - s(56), cx + s(22), cy - s(24)],
        start=20,
        end=160,
        fill=dark,
        width=max(2, s(3)),
    )


def _style_impact(canvas: Image.Image, text: str, subtext: str) -> None:
    draw = ImageDraw.Draw(canvas)
    font = _fit_font(text, 96, 44, WIDTH - 140)
    _draw_text_block(draw, text, font, HEIGHT // 2 - (40 if subtext else 0), stroke_width=8)
    if subtext:
        sub_font = _fit_font(subtext, 46, 26, WIDTH - 160, bold=False)
        _draw_text_block(
            draw, subtext, sub_font, HEIGHT // 2 + 120,
            fill=(255, 244, 214), stroke=(0, 0, 0), stroke_width=4,
        )


def _style_note(canvas: Image.Image, text: str, subtext: str, palette: str) -> None:
    """便签风：浅色纸张 + 横线 + 顶部一条彩色胶带。"""
    paper = Image.new("RGB", (WIDTH, HEIGHT), (255, 251, 235))
    draw = ImageDraw.Draw(paper)
    for y in range(180, HEIGHT - 60, 54):          # 横线
        draw.line([(70, y), (WIDTH - 70, y)], fill=(232, 222, 196), width=2)

    accent = _PALETTES.get(palette, _PALETTES["warm"])[1]
    draw.rectangle([0, 0, WIDTH, 18], fill=accent)  # 顶部色带

    font = _fit_font(text, 72, 34, WIDTH - 160)
    lines = _wrap(draw, text, font, WIDTH - 160)
    y = 120
    for line in lines:
        draw.text((78, y), line, font=font, fill=(60, 52, 40))
        y += font.size + 18

    if subtext:
        sub_font = pillow_font(32, bold=False)
        draw.text((78, y + 20), subtext, font=sub_font, fill=(140, 128, 108))

    draw.text((WIDTH - 150, HEIGHT - 70), "—— 团子龙", font=pillow_font(26, bold=False), fill=(170, 158, 138))
    canvas.paste(paper, (0, 0))


def _style_poster(canvas: Image.Image, text: str, subtext: str) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rectangle([40, 40, WIDTH - 40, HEIGHT - 40], outline=(255, 255, 255, 90), width=3)
    font = _fit_font(text, 84, 40, WIDTH - 200)
    _draw_text_block(draw, text, font, HEIGHT // 2 - 30, stroke=None, line_gap=18)
    draw.line([(WIDTH // 2 - 60, HEIGHT // 2 + 70), (WIDTH // 2 + 60, HEIGHT // 2 + 70)],
              fill=(255, 255, 255, 160), width=3)
    if subtext:
        sub_font = _fit_font(subtext, 38, 24, WIDTH - 240, bold=False)
        _draw_text_block(draw, subtext, sub_font, HEIGHT // 2 + 130, fill=(255, 255, 255), stroke=None)


def _style_dragon(canvas: Image.Image, text: str, subtext: str) -> None:
    _draw_dragon(canvas, (WIDTH // 2, HEIGHT // 2 - 40), scale=1.0)
    draw = ImageDraw.Draw(canvas)
    font = _fit_font(text, 64, 30, WIDTH - 180)
    _draw_text_block(draw, text, font, HEIGHT - 150, stroke_width=6)
    if subtext:
        sub_font = _fit_font(subtext, 32, 20, WIDTH - 200, bold=False)
        _draw_text_block(draw, subtext, sub_font, HEIGHT - 78, fill=(255, 245, 220), stroke=None)


def run(args: dict) -> ToolResult:
    require_pillow()

    text = str(args.get("text", "") or "").strip()
    if not text:
        return ToolResult.failure("要写点什么呀？", hint="请给出图上的文字，例如「周五了」")
    if len(text) > 120:
        text = text[:120] + "…"

    subtext = str(args.get("subtext", "") or "").strip()[:120]
    style = str(args.get("style", "impact") or "impact").strip().lower()
    if style not in ("impact", "note", "poster", "dragon"):
        style = "impact"
    palette = str(args.get("palette", "warm") or "warm").strip().lower()
    top, bottom = _PALETTES.get(palette, _PALETTES["warm"])

    target = output_path("meme")
    try:
        canvas = _gradient(top, bottom)
        if style == "impact":
            _style_impact(canvas, text, subtext)
        elif style == "note":
            _style_note(canvas, text, subtext, palette)
        elif style == "poster":
            _style_poster(canvas, text, subtext)
        else:
            _style_dragon(canvas, text, subtext)

        # 轻微的高光渐变，让画面不那么平
        canvas = canvas.filter(ImageFilter.SMOOTH)
        canvas.save(target, "PNG")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(
            f"生成图片时出错了：{type(exc).__name__}: {exc}",
            hint="可以先用 make_ascii_art 出个文字图，或换个 style 再试",
        )

    style_zh = {"impact": "大字梗图", "note": "便签", "poster": "海报", "dragon": "恐龙海报"}[style]
    caption = f"{text} {subtext}".strip()
    return ToolResult.success(
        f"图做好啦（{style_zh}）：{target.name}",
        data={"path": str(target), "style": style, "palette": palette, "text": text, "subtext": subtext},
        artifacts=[Artifact(kind="image", path=str(target), mime="image/png", caption=caption)],
    )


if __name__ == "__main__":
    for style_name in ("impact", "note", "poster", "dragon"):
        outcome = run({"text": "周五了", "subtext": "代码还没写完", "style": style_name})
        print(outcome.text)
