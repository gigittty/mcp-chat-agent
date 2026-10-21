"""天气查询。

三级降级，保证「永远有话说」
--------------------------
1. **wttr.in** —— 免费、无需 Key，中文城市必须做 URL 编码
2. **open-meteo** —— 主源挂了时用（geocoding 定位 + forecast 取预报），同样免 Key
3. **本地示例数据** —— 断网时的兜底，输出里会**明确标注**「离线示例」，
   绝不假装是实时数据

三个来源都会归一化成同一个 ``data`` 结构，所以上层「查天气→画图」的
跨步引用不会因为降级而取不到字段。
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from agent.contracts import Artifact, ToolResult, ToolSpec  # noqa: F401  (Artifact 供类型参考)
from agent.net import NetworkError, fetch_json, quote_path

SPEC = ToolSpec(
    name="get_weather",
    title="查天气",
    description=(
        "查询指定城市当前天气与未来几天预报。用户问到天气、气温、下雨、冷热、"
        "要不要带伞时就该用这个工具。参数 city 是城市名（中文或英文皆可），"
        "days 是预报天数（1~3）。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名，例如 北京、上海、Hangzhou、Tokyo",
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "default": 1,
                "description": "预报天数，1 表示只要今天",
            },
            "unit": {
                "type": "string",
                "enum": ["c", "f"],
                "default": "c",
                "description": "温度单位：c 摄氏度，f 华氏度",
            },
        },
        "required": ["city"],
    },
    tags=["http", "weather"],
    produces=["weather.series", "weather.days"],
    examples=[{"city": "上海", "days": 3, "unit": "c"}],
)

#: wttr.in 的中文翻译经常没覆盖到，这里自己补一份常见天气的译名
_DESC_ZH = {
    "sunny": "晴",
    "clear": "晴",
    "partly cloudy": "晴间多云",
    "cloudy": "多云",
    "overcast": "阴",
    "mist": "薄雾",
    "fog": "雾",
    "freezing fog": "冻雾",
    "haze": "霾",
    "smoky haze": "烟霾",
    "patchy rain nearby": "局部有雨",
    "patchy rain possible": "可能有雨",
    "light rain": "小雨",
    "moderate rain": "中雨",
    "heavy rain": "大雨",
    "light drizzle": "毛毛雨",
    "light rain shower": "阵雨",
    "moderate or heavy rain shower": "强阵雨",
    "thundery outbreaks possible": "可能有雷阵雨",
    "patchy snow possible": "可能有雪",
    "light snow": "小雪",
    "moderate snow": "中雪",
    "heavy snow": "大雪",
    "blizzard": "暴风雪",
    "sleet": "雨夹雪",
}

#: WMO 天气代码（open-meteo 用）→ 中文
_WMO_ZH = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "毛毛雨", 53: "小雨", 55: "中雨", 56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}


def _translate(text: str) -> str:
    key = str(text or "").strip().lower()
    return _DESC_ZH.get(key, str(text or "").strip() or "未知")


def _to_f(celsius: float) -> int:
    return round(celsius * 9 / 5 + 32)


def _temp(value: float, unit: str) -> int:
    return _to_f(value) if unit == "f" else round(value)


def _unit_symbol(unit: str) -> str:
    return "℉" if unit == "f" else "℃"


# ---------------------------------------------------------------- 主源
def _from_wttr(city: str, days: int) -> dict:
    url = f"https://wttr.in/{quote_path(city)}?format=j1&lang=zh"
    payload = fetch_json(url, timeout=6.0, retries=1)

    current_raw = (payload.get("current_condition") or [{}])[0]
    desc_raw = (current_raw.get("weatherDesc") or [{}])[0].get("value", "")
    current = {
        "temp_c": float(current_raw.get("temp_C", 0)),
        "feels_like_c": float(current_raw.get("FeelsLikeC", 0)),
        "desc": _translate(desc_raw),
        "humidity": int(current_raw.get("humidity", 0)),
        "wind_kmph": int(current_raw.get("windspeedKmph", 0)),
    }

    nearest = (payload.get("nearest_area") or [{}])[0]
    resolved = (nearest.get("areaName") or [{}])[0].get("value") or city

    series = []
    for day_raw in (payload.get("weather") or [])[:days]:
        hourly = day_raw.get("hourly") or []
        midday = hourly[4] if len(hourly) > 4 else (hourly[0] if hourly else {})
        day_desc = (midday.get("weatherDesc") or [{}])[0].get("value", "")
        series.append(
            {
                "date": str(day_raw.get("date", "")),
                "temp_max": float(day_raw.get("maxtempC", 0)),
                "temp_min": float(day_raw.get("mintempC", 0)),
                "desc": _translate(day_desc),
            }
        )

    return {"resolved": resolved, "source": "wttr.in", "degraded": False, "current": current, "days": series}


# ---------------------------------------------------------------- 备源
def _from_open_meteo(city: str, days: int) -> dict:
    geo_url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={quote_path(city)}&count=1&language=zh&format=json"
    )
    geo = fetch_json(geo_url, timeout=6.0, retries=1)
    results = geo.get("results") or []
    if not results:
        raise NetworkError(f"找不到城市：{city}")
    place = results[0]
    lat, lon = place["latitude"], place["longitude"]
    resolved = place.get("name") or city

    wx_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code"
        f"&timezone=auto&forecast_days={days}"
    )
    wx = fetch_json(wx_url, timeout=6.0, retries=1)

    cur = wx.get("current") or {}
    daily = wx.get("daily") or {}
    times = daily.get("time") or []
    code = int(cur.get("weather_code", -1))

    series = []
    for index, day_text in enumerate(times[:days]):
        series.append(
            {
                "date": str(day_text),
                "temp_max": float((daily.get("temperature_2m_max") or [0])[index]),
                "temp_min": float((daily.get("temperature_2m_min") or [0])[index]),
                "desc": _WMO_ZH.get(int((daily.get("weather_code") or [0])[index]), "未知"),
            }
        )

    current = {
        "temp_c": float(cur.get("temperature_2m", 0)),
        "feels_like_c": float(cur.get("apparent_temperature", 0)),
        "desc": _WMO_ZH.get(code, "未知"),
        "humidity": int(cur.get("relative_humidity_2m", 0)),
        "wind_kmph": round(float(cur.get("wind_speed_10m", 0))),
    }
    return {"resolved": resolved, "source": "open-meteo", "degraded": False, "current": current, "days": series}


# ---------------------------------------------------------------- 兜底
def _from_sample(city: str, days: int) -> dict:
    """离线示例数据。用城市名做种子，保证同一城市每次结果一致（可复现）。"""
    seed = int(hashlib.md5(city.encode("utf-8")).hexdigest()[:8], 16)
    base = 12 + seed % 16
    today = date.today()
    series = []
    for offset in range(days):
        wobble = (seed >> (offset * 3)) % 7 - 3
        series.append(
            {
                "date": (today + timedelta(days=offset)).isoformat(),
                "temp_max": float(base + 5 + wobble),
                "temp_min": float(base - 3 + wobble),
                "desc": ["晴", "多云", "阴", "小雨"][(seed + offset) % 4],
            }
        )
    current = {
        "temp_c": float(base),
        "feels_like_c": float(base - 1),
        "desc": series[0]["desc"],
        "humidity": 40 + seed % 50,
        "wind_kmph": 5 + seed % 20,
    }
    return {"resolved": city, "source": "offline-sample", "degraded": True, "current": current, "days": series}


_ASSETS: list[tuple[str, object]] = [
    ("wttr.in", _from_wttr),
    ("open-meteo", _from_open_meteo),
]


def run(args: dict) -> ToolResult:
    city = str(args.get("city", "")).strip()
    if not city:
        return ToolResult.failure("还没告诉我要查哪个城市", hint="请补充城市名，例如「北京」")

    try:
        days = int(args.get("days", 1))
    except (TypeError, ValueError):
        days = 1
    days = max(1, min(days, 3))

    unit = str(args.get("unit", "c") or "c").strip().lower()
    if unit not in ("c", "f"):
        unit = "c"

    errors: list[str] = []
    payload: dict | None = None
    for source_name, loader in _ASSETS:
        try:
            payload = loader(city, days)  # type: ignore[operator]
            break
        except Exception as exc:  # noqa: BLE001 - 任何一个源失败都应该继续降级
            errors.append(f"{source_name}: {type(exc).__name__}")
            payload = None

    if payload is None:
        payload = _from_sample(city, days)

    current = payload["current"]
    symbol = _unit_symbol(unit)
    # 显示用户自己说的城市名：wttr.in 的行政区名经常是英文区名（上海→Pootung），
    # 直接展示会让用户困惑，所以只在数据里保留解析结果。
    display_city = city
    lines = [
        f"{display_city} 现在 {_temp(current['temp_c'], unit)}{symbol}，{current['desc']}",
        f"体感 {_temp(current['feels_like_c'], unit)}{symbol}，"
        f"湿度 {current['humidity']}%，风速 {current['wind_kmph']} km/h",
    ]
    if payload["days"]:
        lines.append("未来天气：")
        for day in payload["days"]:
            lines.append(
                f"  {day['date']}  {day['desc']}  "
                f"{_temp(day['temp_min'], unit)}~{_temp(day['temp_max'], unit)}{symbol}"
            )

    if payload["source"] == "offline-sample":
        lines.append("（注意：这是离线示例数据，不是实时天气哦）")
    elif payload["source"] == "open-meteo":
        lines.append("（wttr.in 暂时连不上，已自动切换到备用数据源 open-meteo）")

    data = {
        "city": display_city,
        "resolved_name": payload.get("resolved", display_city),
        "source": payload["source"],
        "degraded": payload["degraded"],
        "unit": unit,
        "current": {
            **current,
            "temp": _temp(current["temp_c"], unit),
            "feels_like": _temp(current["feels_like_c"], unit),
        },
        "days": [
            {
                **day,
                "temp_max_display": _temp(day["temp_max"], unit),
                "temp_min_display": _temp(day["temp_min"], unit),
            }
            for day in payload["days"]
        ],
        "series": {
            "date": [day["date"] for day in payload["days"]],
            "temp_max": [_temp(day["temp_max"], unit) for day in payload["days"]],
            "temp_min": [_temp(day["temp_min"], unit) for day in payload["days"]],
            "desc": [day["desc"] for day in payload["days"]],
        },
        "fallback_errors": errors,
    }
    return ToolResult.success("\n".join(lines), data=data)


if __name__ == "__main__":
    import sys

    city = sys.argv[1] if len(sys.argv) > 1 else "北京"
    result = run({"city": city, "days": 3})
    print(result.text)
    print("source:", result.data.get("source"))
