"""火山方舟 (Volcengine Ark) 客户端 — OpenAI 兼容格式

认证方式: Authorization: Bearer <ARK_API_KEY>
Base URL 示例: https://ark.cn-beijing.volces.com/api/plan/v3
                          https://ark.cn-beijing.volces.com/api/v3

推理型模型默认开启 thinking，响应/流式块中会带 reasoning_content；
本客户端显式关闭 thinking（仅发正文 content，避免浪费 token 与干扰前端）。
"""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

import httpx

logger = logging.getLogger(__name__)


class ArkClient:
    """火山方舟 API 客户端"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/plan/v3",
        model: str = "ark-code-latest",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        messages: list[dict],
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
        stream: bool = False,
    ) -> dict:
        all_messages: list[dict] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        payload = {
            "model": model or self.model,
            "messages": all_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "thinking": {"type": "disabled"},
        }
        if stream:
            payload["stream"] = True
        return payload

    async def chat(
        self,
        messages: list[dict],
        system: str = "",
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> dict:
        """同步对话，返回 {content: str, model: str, usage: dict}"""
        client = await self._get_client()
        payload = self._build_payload(messages, system, model, max_tokens, temperature)

        resp = await client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Ark API {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError(f"Ark API 返回空 choices: {data}")

        message = choices[0].get("message") or {}
        return {
            "content": message.get("content") or "",
            "model": data.get("model", payload["model"]),
            "usage": data.get("usage", {}),
        }

    async def chat_stream(
        self,
        messages: list[dict],
        system: str = "",
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """流式对话，逐块 yield 正文 content（忽略 reasoning_content）"""
        client = await self._get_client()
        payload = self._build_payload(messages, system, model, max_tokens, temperature, stream=True)

        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="ignore")
                raise RuntimeError(f"Ark API {resp.status_code}: {body[:300]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
