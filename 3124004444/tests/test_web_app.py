"""Streamlit 界面的无头测试。

用官方 ``AppTest`` 真正执行页面脚本并模拟一次对话——比只做 import 检查
可靠得多：它能抓到「组件参数写错」「会话状态用错」这类只在运行时才暴露的问题。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="未安装 streamlit，跳过网页界面测试")

from streamlit.testing.v1 import AppTest  # noqa: E402

# AppTest 的相对路径是相对「调用它的文件」解析的，所以这里用绝对路径
APP = Path(__file__).resolve().parent.parent / "agent" / "ui" / "web_app.py"


@pytest.fixture(scope="module")
def app() -> AppTest:
    instance = AppTest.from_file(str(APP), default_timeout=180)
    instance.run()
    return instance


def test_页面能正常渲染(app: AppTest) -> None:
    assert not app.exception
    assert app.title
    assert "团子龙" in app.title[0].value


def test_侧栏有模式与通道选择(app: AppTest) -> None:
    assert len(app.sidebar.radio) == 2
    labels = [radio.label for radio in app.sidebar.radio]
    assert "规划器" in labels
    assert "工具通道" in labels


def test_侧栏列出八个工具(app: AppTest) -> None:
    joined = "\n".join(item.value for item in app.sidebar.markdown)
    for name in ("calculate", "get_weather", "make_chart", "make_meme", "files"):
        assert name in joined


def test_一次完整对话(app: AppTest) -> None:
    app.chat_input[0].set_value("算一下 (12+8)*3/5 保留两位小数").run()

    assert not app.exception
    assert not app.error
    body = "\n".join(item.value for item in app.markdown)
    assert "calculate" in body          # 工具卡片
    assert "= 12" in body               # 最终回复
    # 走的是哪条通道会显示在工具卡片上
    assert "via-mcp" in body or "via-builtin" in body
