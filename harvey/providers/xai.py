"""xAI Grok provider implementation with strict paid-usage authorization safeguards."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from harvey.providers.base import AIProvider, PaidProviderNotApprovedError, ProviderResult

logger = logging.getLogger("harvey.providers.xai")

XAI_API_URL = "https://api.x.ai/v1/chat/completions"


class XAIProvider(AIProvider):
    """xAI Grok provider. PAID ONLY — Requires explicit operator approval before use."""

    name = "xai"
    label = "xAI (Grok)"
    is_free = False
    is_paid = True
    requires_key = True
    env_key = "XAI_API_KEY"
    default_model = "grok-2-1212"

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        super().__init__(config, env)

    @property
    def is_paid_approved(self) -> bool:
        """Verify whether the operator explicitly approved paid usage for xAI."""
        return bool(self.config.get("xai_approved_paid", False))

    async def preflight(self) -> str:
        """Check key and strict paid approval."""
        key = self.get_api_key()
        if not key:
            return (
                "XAI_API_KEY is not configured in .env. "
                "xAI is a paid provider. Get an API key at https://console.x.ai"
            )
        if not self.is_paid_approved:
            return (
                "xAI Grok is a PAID provider and will incur billing. Paid usage has NOT been approved. "
                "To enable xAI, set 'brain.xai_approved_paid: true' in harvey.yaml or approve in settings."
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
        """Call xAI chat completions endpoint with strict billing gate."""
        target_model = model or self.default_model

        # STRICT SAFEGUARD: Never automatically spend money or enable billing
        if not self.is_paid_approved:
            msg = (
                "Call to xAI Grok blocked: Paid usage has not been approved by the operator. "
                "Harvey refuses to silently incur billing. Set 'xai_approved_paid: true' to approve."
            )
            logger.error(msg)
            raise PaidProviderNotApprovedError(msg)

        key = self.get_api_key()
        if not key:
            return ProviderResult(
                text="",
                provider=self.name,
                model=target_model,
                is_error=True,
                error_message="XAI_API_KEY missing.",
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
                res = await client.post(XAI_API_URL, headers=headers, json=payload)
                duration_ms = int((time.monotonic() - start_time) * 1000)

                if res.status_code != 200:
                    msg = f"xAI API error (HTTP {res.status_code}): {res.text[:200]}"
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
                # Estimate cost for grok-2 ($2 per 1M in, $10 per 1M out approx)
                cost_usd = (prompt_tokens * 0.000002) + (completion_tokens * 0.000010)

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
            msg = f"xAI call failed: {e}"
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

