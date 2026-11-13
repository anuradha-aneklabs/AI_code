"""Google Gemini API provider implementation (Free tier enabled)."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from harvey.providers.base import AIProvider, ProviderResult

logger = logging.getLogger("harvey.providers.gemini")

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(AIProvider):
    """Google Gemini API provider supporting free-tier Flash and Pro models."""

    name = "gemini"
    label = "Google Gemini"
    is_free = True
    is_paid = False
    requires_key = True
    env_key = "GEMINI_API_KEY"
    default_model = "gemini-2.0-flash"

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        super().__init__(config, env)

    def get_api_key(self) -> str:
        key = (self.env.get("GEMINI_API_KEY") or self.env.get("GOOGLE_API_KEY") or "").strip()
        return key

    async def preflight(self) -> str:
        """Check if Gemini API key is configured."""
        key = self.get_api_key()
        if not key:
            return (
                "GEMINI_API_KEY is not configured in .env. "
                "Get a free API key at https://aistudio.google.com/app/apikey"
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
        """Call Google Gemini generateContent endpoint."""
        target_model = model or self.default_model
        key = self.get_api_key()
        if not key:
            return ProviderResult(
                text="",
                provider=self.name,
                model=target_model,
                is_error=True,
                error_message="GEMINI_API_KEY missing. Get a key at https://aistudio.google.com",
                task=task,
                agent=agent,
            )

        url = f"{GEMINI_API_BASE}/models/{target_model}:generateContent?key={key}"
        generation_config: dict[str, Any] = {"temperature": 0.2}
        if expect_json:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

        start_time = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                duration_ms = int((time.monotonic() - start_time) * 1000)

                if res.status_code == 429:
                    msg = (
                        f"Google Gemini rate limit / quota exceeded (HTTP 429). "
                        f"Free tier limit is 15 RPM. Wait a moment or fallback."
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
                    msg = f"Gemini API error (HTTP {res.status_code}): {res.text[:200]}"
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
                candidates = data.get("candidates") or []
                text = ""
                if candidates:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    if parts:
                        text = (parts[0].get("text") or "").strip()

                usage = data.get("usageMetadata") or {}
                prompt_tokens = int(usage.get("promptTokenCount") or 0)
                completion_tokens = int(usage.get("candidatesTokenCount") or 0)

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
            msg = f"Gemini call error: {e}"
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

