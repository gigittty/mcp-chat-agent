"""天气工具测试。

不打真实网络：全部通过 OFFLINE 开关走兜底分支，验证
「降级链能兜住」「输出如实标注」「数据结构保持稳定」三件事。
"""

from __future__ import annotations

import pytest

from agent.tools import weather


@pytest.fixture()
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制离线，避免测试依赖外网。"""
    monkeypatch.setenv("AGENT_OFFLINE", "1")


def test_缺城市(offline, data_root) -> None:
    result = weather.run({})
    assert result.ok is False
    assert result.hint


def test_离线时使用示例数据并如实标注(offline, data_root) -> None:
    result = weather.run({"city": "北京"})
    assert result.ok
    assert result.data["source"] == "offline-sample"
    assert result.data["degraded"] is True
    assert "离线示例" in result.text


def test_同一城市结果可复现(offline, data_root) -> None:
    first = weather.run({"city": "杭州"})
    second = weather.run({"city": "杭州"})
    assert first.data["current"]["temp_c"] == second.data["current"]["temp_c"]


def test_days_参数生效(offline, data_root) -> None:
    one = weather.run({"city": "上海", "days": 1})
    three = weather.run({"city": "上海", "days": 3})
    assert len(one.data["days"]) == 1
    assert len(three.data["days"]) == 3


def test_days_越界被钳制(offline, data_root) -> None:
    result = weather.run({"city": "上海", "days": 99})
    assert len(result.data["days"]) == 3


def test_华氏度换算(offline, data_root) -> None:
    celsius = weather.run({"city": "广州", "unit": "c"})
    fahrenheit = weather.run({"city": "广州", "unit": "f"})
    c = celsius.data["current"]["temp"]
    f = fahrenheit.data["current"]["temp"]
    assert f == round(c * 9 / 5 + 32)
    assert "℉" in fahrenheit.text


def test_跨步引用所需字段齐全(offline, data_root) -> None:
    """「查天气→画图」依赖 series 字段，降级后结构必须一致。"""
    data = weather.run({"city": "成都", "days": 3}).data
    series = data["series"]
    assert len(series["date"]) == 3
    assert len(series["temp_max"]) == 3
    assert len(series["temp_min"]) == 3
    assert len(series["desc"]) == 3


def test_英文城市名可用(offline, data_root) -> None:
    assert weather.run({"city": "Tokyo"}).ok
