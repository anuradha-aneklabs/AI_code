"""Codex provider adapter preserving existing Codex app-server integration."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from harvey.providers.base import AIProvider, ProviderResult

logger = logging.getLogger("harvey.providers.codex")


class CodexProvider(AIProvider):
    """Adapter for ChatGPT subscription via isolated Codex app-server."""

    name = "codex"
    label = "Codex"
    is_free = False
    is_paid = False  # Covered by flat ChatGPT subscription
    requires_key = False
    default_model = "gpt-5.5"

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        super().__init__(config, env)
        from harvey.codex import CodexClient

        model = (config or {}).get("model", "")
        reasoning_effort = (config or {}).get("reasoning_effort", "")
        self.codex_client = CodexClient(model=model, reasoning_effort=reasoning_effort)

    async def preflight(self) -> str:
        """Run Codex preflight checks."""
        try:
            await self.codex_client.preflight()
            return ""
        except Exception as e:
            return str(e)

    async def generate(
        self,
        prompt: str,
        model: str = "",
        expect_json: bool = False,
        timeout: float = 300.0,
        task: str = "",
        agent: str = "",
    ) -> ProviderResult:
        """Run Codex prompt execution."""
        from harvey.codex import CodexError

        start_time = time.monotonic()
        target_model = model or self.config.get("model", "")

        try:
            result = await self.codex_client.run(prompt, timeout=timeout)
            duration_ms = int((time.monotonic() - start_time) * 1000)
            usage = result.usage or {}

            return ProviderResult(
                text=result.text,
                provider=self.name,
                model=result.model or target_model,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cost_usd=0.0,
                duration_ms=duration_ms,
                task=task,
                agent=agent,
                metadata={"thread_id": result.thread_id},
            )
        except CodexError as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return ProviderResult(
                text="",
                provider=self.name,
                model=target_model,
                duration_ms=duration_ms,
                is_error=True,
                error_message=str(e),
                task=task,
                agent=agent,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return ProviderResult(
                text="",
                provider=self.name,
                model=target_model,
                duration_ms=duration_ms,
                is_error=True,
                error_message=str(e),
                task=task,
                agent=agent,
            )

