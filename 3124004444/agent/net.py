"""极简 HTTP 客户端（基于标准库 urllib）。

为什么不用 requests
------------------
只需要 GET 一个 JSON，requests 带来的收益不足以抵消「多一个依赖、
多一类证书问题」的成本。urllib 在 Windows 上的证书行为也更可预测。

统一在这里处理：UA（很多站点拒绝空 UA）、超时、重试、离线开关、JSON 解析。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 5.0
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) mcp-chat-agent/0.1"


class NetworkError(Exception):
    """网络请求失败（已重试）。"""


def is_offline() -> bool:
    """离线开关：配置里的 OFFLINE=1，或环境变量 AGENT_OFFLINE=1。"""
    import os

    from agent.config import get_settings

    if os.environ.get("AGENT_OFFLINE", "") in ("1", "true", "yes"):
        return True
    return get_settings().offline


def _build_request(url: str, headers: dict[str, str] | None = None) -> urllib.request.Request:
    merged = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if headers:
        merged.update(headers)
    return urllib.request.Request(url, headers=merged)


def fetch_text(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 1,
    headers: dict[str, str] | None = None,
) -> str:
    """GET 一个 URL 并返回文本。失败会重试，最终抛 NetworkError。"""
    if is_offline():
        raise NetworkError("当前处于离线模式")

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = _build_request(url, headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 4xx 是请求本身有问题，重试无意义
            if 400 <= exc.code < 500 and exc.code != 429:
                break
        except Exception as exc:  # noqa: BLE001 - 超时/DNS/连接重置统一处理
            last_error = exc
        if attempt < retries:
            time.sleep(0.4 * (attempt + 1))

    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "未知错误"
    raise NetworkError(f"请求失败（{detail}）")


def fetch_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 1,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET 并解析 JSON。"""
    text = fetch_text(url, timeout=timeout, retries=retries, headers=headers)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise NetworkError(f"返回的不是合法 JSON：{exc}") from exc


def quote_path(text: str) -> str:
    """把城市名等中文安全地放进 URL 路径。

    wttr.in 对未编码的中文会返回 404，必须做 percent-encoding。
    """
    return urllib.parse.quote(str(text), safe="")


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = 60.0,
    headers: dict[str, str] | None = None,
) -> Any:
    """POST JSON 并解析 JSON 响应。用于调用大模型的 chat/completions。

    默认不重试：大模型调用失败重试可能重复计费，而且语义上「一次请求
    一次结果」更符合预期。错误信息会带上响应体，方便定位 401/429。
    """
    if is_offline():
        raise NetworkError("当前处于离线模式，无法调用在线大模型")

    merged = {"User-Agent": DEFAULT_UA, "Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        merged.update(headers)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=merged, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        raise NetworkError(f"HTTP {exc.code}：{detail or exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001
        raise NetworkError(f"请求失败：{type(exc).__name__}: {exc}") from exc
