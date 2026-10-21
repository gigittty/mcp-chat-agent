"""配置读取。

不依赖 python-dotenv：自己解析 .env，少一个第三方包、少一类安装问题。
优先级：环境变量 > .env 文件 > 默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from agent import CODE_ROOT

ENV_FILE: Path = CODE_ROOT / ".env"

_TRUE = {"1", "true", "yes", "on", "y"}


def _parse_env_file(path: Path) -> dict[str, str]:
    """解析最简单的 KEY=VALUE 形式，支持 # 注释与引号。"""
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            data[key] = value
    return data


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in _TRUE


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """全局配置。构建一次即可，纯数据、可测试。"""

    agent_mode: str = "mock"        # mock | llm
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    mcp_channel: str = "auto"       # auto | mcp | builtin
    offline: bool = False
    max_steps: int = 6
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def llm_enabled(self) -> bool:
        """真能走 LLM 的条件：显式切到 llm 且 Key 非空。"""
        return self.agent_mode.lower() == "llm" and bool(self.llm_api_key)

    @property
    def mode_label(self) -> str:
        """给界面显示用的中文标签。"""
        if self.agent_mode.lower() == "llm":
            return f"大模型模式（{self.llm_model}）" if self.llm_api_key else "大模型模式（缺少 API Key，已回落 mock）"
        return "离线规则模式（无需 API Key）"


def load_settings(env_file: Path | None = None) -> Settings:
    """读取配置。环境变量优先于 .env。"""
    file_values = _parse_env_file(env_file or ENV_FILE)

    def get(key: str, default: str = "") -> str:
        env_value = os.environ.get(key)
        if env_value is not None and env_value != "":
            return env_value
        return file_values.get(key, default)

    mode = (get("AGENT_MODE", "mock") or "mock").strip().lower()
    if mode not in ("mock", "llm"):
        mode = "mock"

    channel = (get("MCP_CHANNEL", "auto") or "auto").strip().lower()
    if channel not in ("auto", "mcp", "builtin"):
        channel = "auto"

    return Settings(
        agent_mode=mode,
        llm_base_url=get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
        llm_api_key=get("LLM_API_KEY", "").strip(),
        llm_model=get("LLM_MODEL", "deepseek-chat").strip(),
        mcp_channel=channel,
        offline=_as_bool(get("OFFLINE", "0")),
        max_steps=max(1, min(_as_int(get("MAX_STEPS", "6"), 6), 12)),
        extras=file_values,
    )


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """进程内单例。"""
    global _settings
    if _settings is None or refresh:
        _settings = load_settings()
    return _settings


def use_settings(settings: Settings) -> Settings:
    """把某份配置设为进程内生效的配置。

    命令行/网页传进来的临时配置必须走到这里，否则工具侧通过
    :func:`get_settings` 读到的还是旧的单例（曾因此让 --offline 不生效）。

    离线开关额外写进环境变量：MCP 服务端是**子进程**，拿不到父进程的
    Python 对象，只能靠环境变量传过去。
    """
    global _settings
    _settings = settings
    if settings.offline:
        os.environ["AGENT_OFFLINE"] = "1"
    else:
        os.environ.pop("AGENT_OFFLINE", None)
    if settings.max_steps:
        os.environ.setdefault("MAX_STEPS", str(settings.max_steps))
    return settings
