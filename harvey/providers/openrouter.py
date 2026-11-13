"""OpenRouter provider implementation supporting free open models."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from harvey.providers.base import AIProvider, ProviderResult

logger = logging.getLogger("harvey.providers.openrouter")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(AIProvider):
    """OpenRouter provider supporting free community models."""

    name = "openrouter"
    label = "OpenRouter"
    is_free = True
    is_paid = False
    requires_key = True
    env_key = "OPENROUTER_API_KEY"
    default_model = "meta-llama/llama-3.3-70b-instruct:free"

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        super().__init__(config, env)

    async def preflight(self) -> str:
        """Check if OpenRouter key is provided."""
        key = self.get_api_key()
        if not key:
            return (
                "OPENROUTER_API_KEY is not configured in .env. "
                "Get a key at https://openrouter.ai/keys"
            )
        return ""

    async def generate(
        self,
        prompt: str,
        model: str = "",
        expect_json: bool = False,
        timeout: float = 90.0,
        task: str = "",
        agent: str = "",
    ) -> ProviderResult:
        """Call OpenRouter completions endpoint."""
        target_model = model or self.default_model
        key = self.get_api_key()
        if not key:
            return ProviderResult(
                text="",
                provider=self.name,
                model=target_model,
                is_error=True,
                error_message="OPENROUTER_API_KEY missing. Get a key at https://openrouter.ai/keys",
                task=task,
                agent=agent,
            )

        headers = {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://github.com/ethanplusai/harvey",
            "X-Title": "Harvey Sales Agent",
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
                res = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
                duration_ms = int((time.monotonic() - start_time) * 1000)

                if res.status_code == 429:
                    msg = "OpenRouter rate limit exceeded (HTTP 429). Will retry or fallback."
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
                    msg = f"OpenRouter API error (HTTP {res.status_code}): {res.text[:200]}"
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
                # Free models have 0 dollar cost
                cost_usd = 0.0 if target_model.endswith(":free") else float(data.get("usage", {}).get("total_cost", 0.0))

                return ProviderResult(
                    text=text,
                    provider=self.name,
                    model=target_model,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    task=task,
                    agent=agent,
                )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            msg = f"OpenRouter call failed: {e}"
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

