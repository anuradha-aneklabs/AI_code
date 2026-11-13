"""Groq API provider implementation for ultra-fast LPU inference (Free tier)."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from harvey.providers.base import AIProvider, ProviderResult

logger = logging.getLogger("harvey.providers.groq")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(AIProvider):
    """Groq LPU provider supporting fast open-weights inference on free tier."""

    name = "groq"
    label = "Groq"
    is_free = True
    is_paid = False
    requires_key = True
    env_key = "GROQ_API_KEY"
    default_model = "llama-3.3-70b-versatile"

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        super().__init__(config, env)

    async def preflight(self) -> str:
        """Check if Groq API key is present."""
        key = self.get_api_key()
        if not key:
            return (
                "GROQ_API_KEY is not configured in .env. "
                "Get a free API key at https://console.groq.com/keys"
            )
        return ""

    async def generate(
        self,
        prompt: str,
        model: str = "",
        expect_json: bool = False,
        timeout: float = 60.0,
        task: str = "",
        agent: str = "",
    ) -> ProviderResult:
        """Call Groq chat completions endpoint."""
        target_model = model or self.default_model
        key = self.get_api_key()
        if not key:
            return ProviderResult(
                text="",
                provider=self.name,
                model=target_model,
                is_error=True,
                error_message="GROQ_API_KEY missing. Get a key at https://console.groq.com/keys",
                task=task,
                agent=agent,
            )

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        if expect_json:
            payload["response_format"] = {"type": "json_object"}

        start_time = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(GROQ_API_URL, headers=headers, json=payload)
                duration_ms = int((time.monotonic() - start_time) * 1000)

                if res.status_code == 429:
                    msg = (
                        "Groq rate limit exceeded (HTTP 429). Free tier rate limits reached. "
                        "Requests will wait or fall back to an alternate free provider."
                    )
                    logger.warning(msg)
                    return ProviderResult(
                        text="",
                        provider=self.name,
                        model=target_model,
                        duration_ms=duration_ms,
                        is_error=True,
                        error_message=msg,
                        task=task,
                        agent=agent,
                    )

                if res.status_code != 200:
                    msg = f"Groq API error (HTTP {res.status_code}): {res.text[:200]}"
                    logger.error(msg)
                    return ProviderResult(
                        text="",
                        provider=self.name,
                        model=target_model,
                        duration_ms=duration_ms,
                        is_error=True,
                        error_message=msg,
                        task=task,
                        agent=agent,
                    )

                data = res.json()
                choices = data.get("choices") or []
                text = ""
                if choices:
                    text = (choices[0].get("message") or {}).get("content", "").strip()

                usage = data.get("usage") or {}
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)

                return ProviderResult(
                    text=text,
                    provider=self.name,
                    model=target_model,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    cost_usd=0.0,
                    duration_ms=duration_ms,
                    task=task,
                    agent=agent,
                )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            msg = f"Groq call failed: {e}"
            logger.error(msg)
            return ProviderResult(
                text="",
                provider=self.name,
                model=target_model,
                duration_ms=duration_ms,
                is_error=True,
                error_message=msg,
                task=task,
                agent=agent,
            )

