"""OpenAI 兼容的对话客户端。

刻意不引入 ``openai`` 官方包：只需要一次 POST，用 urllib 就够了，
少一个依赖、少一类版本冲突。任何兼容 OpenAI ``/chat/completions``
的服务都能用（DeepSeek、智谱 GLM、阿里百炼、本地 vLLM…），
换服务商只改 ``.env`` 里的三个变量。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.config import Settings
from agent.errors import AgentError
from agent.net import NetworkError, post_json

logger = logging.getLogger(__name__)


class LLMError(AgentError):
    """大模型调用失败。"""


class OpenAICompatClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = (settings.llm_base_url or "").rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = 60.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """调用 chat/completions，返回首个 choice 的 message 字典。"""
        if not self.configured:
            raise LLMError("未配置大模型：请在 .env 里填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools

        try:
            response = post_json(
                f"{self.base_url}/chat/completions",
                payload,
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except NetworkError as exc:
            raise LLMError(f"调用大模型失败：{exc}") from exc

        choices = response.get("choices") or []
        if not choices:
            raise LLMError(f"大模型没有返回结果：{str(response)[:300]}")
        message = choices[0].get("message") or {}
        logger.debug("LLM 返回：%s", str(message)[:500])
        return message
