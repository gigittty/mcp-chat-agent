"""生成图片类工具的测试。

这里不比对像素，只验证四件容易出问题的事：
1. 真的产出了文件，且文件非空
2. 中文字体链路没有退化成方块（用「能不能画出字」间接判断）
3. 参数不合法时返回失败而不是抛异常
4. labels/values 长度不一致时能优雅截断
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.tools import ascii_art, chart, meme
from agent.tools.fonts import find_cjk_font_file


def _exists(path_text: str) -> bool:
    path = Path(path_text)
    return path.is_file() and path.stat().st_size > 0


# ------------------------------------------------------------------ 图表
def test_图表缺少数据(data_root: Path) -> None:
    assert chart.run({}).ok is False
    assert chart.run({"labels": ["a"]}).ok is False
    assert chart.run({"values": [1]}).ok is False


@pytest.mark.parametrize("kind", ["bar", "line", "pie"])
def test_三种图表都能生成(data_root: Path, kind: str) -> None:
    result = chart.run(
        {
            "title": "测试图表",
            "labels": ["周一", "周二", "周三"],
            "values": [3, 7, 5],
            "kind": kind,
        }
    )
    assert result.ok, result.error
    assert _exists(result.data["path"])
    assert result.artifacts[0].kind == "image"


def test_标签与数值长度不一致时截断(data_root: Path) -> None:
    result = chart.run({"labels": ["a", "b", "c"], "values": [1, 2]})
    assert result.ok
    assert result.data["n"] == 2


def test_数值含非数字时报错(data_root: Path) -> None:
    result = chart.run({"labels": ["a"], "values": ["不是数字"]})
    assert result.ok is False
    assert result.hint


def test_非法图表类型回退为柱状图(data_root: Path) -> None:
    result = chart.run({"labels": ["a", "b"], "values": [1, 2], "kind": "3d-rotate"})
    assert result.ok
    assert result.data["kind"] == "bar"


def test_标签过多时旋转不报错(data_root: Path) -> None:
    labels = [f"第{i}天" for i in range(15)]
    values = list(range(15))
    assert chart.run({"labels": labels, "values": values}).ok


# ------------------------------------------------------------------ 梗图
def test_梗图缺少文字(data_root: Path) -> None:
    assert meme.run({}).ok is False


@pytest.mark.parametrize("style", ["impact", "note", "poster", "dragon"])
def test_四种版式都能生成(data_root: Path, style: str) -> None:
    result = meme.run({"text": "周五了", "subtext": "代码还没写完", "style": style})
    assert result.ok, result.error
    assert _exists(result.data["path"])


def test_超长文字被截断(data_root: Path) -> None:
    result = meme.run({"text": "很长的文字" * 50})
    assert result.ok
    assert len(result.data["text"]) <= 121


def test_非法版式回退(data_root: Path) -> None:
    result = meme.run({"text": "测试", "style": "不存在的风格"})
    assert result.ok
    assert result.data["style"] == "impact"


def test_中文图片不会用方块字体(data_root: Path) -> None:
    """本机必须有可用中文字体，否则生成的图会是方块，视觉上等于失败。"""
    assert find_cjk_font_file() is not None, "环境里找不到任何可用中文字体"


# ------------------------------------------------------------------ ASCII
def test_ascii_基本渲染(data_root: Path) -> None:
    result = ascii_art.run({"text": "HI"})
    assert result.ok
    art = result.data["art"]
    assert "#" in art
    assert len(art.splitlines()) == 7
    assert _exists(result.data["path"])


def test_ascii_未知字符用问号占位(data_root: Path) -> None:
    result = ascii_art.run({"text": "你好"})
    assert result.ok
    assert result.data["missing_chars"] == 2
    assert "占位" in result.text


def test_ascii_放大倍数(data_root: Path) -> None:
    normal = ascii_art.run({"text": "A", "scale": 1})
    bigger = ascii_art.run({"text": "A", "scale": 3})
    assert bigger.data["height"] == normal.data["height"] * 3


def test_ascii_自定义填充字符(data_root: Path) -> None:
    result = ascii_art.run({"text": "A", "fill": "█"})
    assert "█" in result.data["art"]
    assert "#" not in result.data["art"]


def test_ascii_缺少文字(data_root: Path) -> None:
    assert ascii_art.run({}).ok is False
