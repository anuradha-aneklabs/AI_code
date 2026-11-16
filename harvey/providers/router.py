"""Task-based router and intelligent fallback engine for Harvey AI Multi-Provider system."""

from __future__ import annotations

import logging
from typing import Any, Optional

from harvey.providers.base import AIProvider, PaidProviderNotApprovedError, ProviderResult
from harvey.providers.claude_adapter import ClaudeProvider
from harvey.providers.codex_adapter import CodexProvider
from harvey.providers.gemini import GeminiProvider
from harvey.providers.groq import GroqProvider
from harvey.providers.ollama import OllamaProvider
from harvey.providers.openrouter import OpenRouterProvider
from harvey.providers.registry import ModelRegistry
from harvey.providers.xai import XAIProvider

logger = logging.getLogger("harvey.providers.router")


PROVIDER_CLASSES: dict[str, type[AIProvider]] = {
    "ollama": OllamaProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "xai": XAIProvider,
    "claude": ClaudeProvider,
    "codex": CodexProvider,
}


class TaskRouter:
    """Manages AI provider instantiation, task routing, and intelligent fallback."""

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        self.config = config or {}
        self.env = env or {}
        self.primary_name: str = str(self.config.get("provider") or "ollama").strip().lower()
        self.configured_model: str = str(self.config.get("model") or "").strip()
        self.task_routing_enabled: bool = bool(self.config.get("task_routing", True))
        self.fallback_names: list[str] = [
            str(p).strip().lower() for p in (self.config.get("fallback_providers") or []) if str(p).strip()
        ]
        self._provider_instances: dict[str, AIProvider] = {}

    def get_provider(self, name: str) -> AIProvider:
        """Instantiate or retrieve a cached provider instance."""
        norm_name = name.lower().strip()
        if norm_name not in self._provider_instances:
            cls = PROVIDER_CLASSES.get(norm_name)
            if not cls:
                raise ValueError(
                    f"Unknown AI provider '{name}'. "
                    f"Supported providers: {', '.join(sorted(PROVIDER_CLASSES.keys()))}."
                )
            self._provider_instances[norm_name] = cls(config=self.config, env=self.env)
        return self._provider_instances[norm_name]

    def resolve_model(self, provider_name: str, task: str = "") -> str:
        """Resolve the model to use for a given provider and task."""
        if self.configured_model:
            return self.configured_model
        if self.task_routing_enabled and task:
            return ModelRegistry.select_model_for_task(provider_name, task=task)
        return ModelRegistry.get_default_model(provider_name)

    async def execute(
        self,
        prompt: str,
        task: str = "",
        agent: str = "",
        expect_json: bool = False,
        timeout: float = 60.0,
    ) -> ProviderResult:
        """Execute a prompt with task routing and safe fallback.

        Rules:
        1. Try primary provider first.
        2. If primary succeeds, return its result.
        3. If primary fails, check fallback_providers list.
        4. NEVER silently fall back to a paid provider (xAI).
        5. Mark fallback_used = True so callers know which model produced the answer.
        """
        primary = self.get_provider(self.primary_name)
        model = self.resolve_model(self.primary_name, task=task)

        logger.debug(f"Executing AI call via {self.primary_name} ({model}) for task='{task}'")

        primary_result = await primary.generate(
            prompt=prompt,
            model=model,
            expect_json=expect_json,
            timeout=timeout,
            task=task,
            agent=agent,
        )

        if not primary_result.is_error and primary_result.text:
            return primary_result

        # Primary failed; evaluate fallback options
        primary_error = primary_result.error_message or "empty response"
        logger.warning(
            f"Primary AI provider '{self.primary_name}' failed for task='{task}': {primary_error}. "
            f"Checking fallback providers..."
        )

        candidate_fallbacks = [p for p in self.fallback_names if p != self.primary_name]
        # If no fallbacks configured, check default free cloud providers (groq, gemini, openrouter)
        if not candidate_fallbacks:
            candidate_fallbacks = [p for p in ["groq", "gemini", "openrouter"] if p != self.primary_name]

        # Always append local Ollama as the resilient zero-cost fallback
        if "ollama" not in candidate_fallbacks and self.primary_name != "ollama":
            candidate_fallbacks.append("ollama")

        for fb_name in candidate_fallbacks:
            # STRICT SAFEGUARD: Never fall back to xAI or paid providers
            if fb_name == "xai" and not self.config.get("xai_approved_paid"):
                logger.debug("Skipping xAI in fallback: paid usage not approved.")
                continue

            try:
                fb_provider = self.get_provider(fb_name)
            except ValueError:
                continue

            # Only fall back to providers that pass preflight (configured with key / reachable)
            preflight_err = await fb_provider.preflight()
            if preflight_err:
                logger.debug(f"Fallback provider '{fb_name}' not ready: {preflight_err}")
                continue

            fb_model = self.resolve_model(fb_name, task=task)
            logger.info(
                f"Falling back to '{fb_name}' ({fb_model}) because '{self.primary_name}' failed."
            )

            fb_result = await fb_provider.generate(
                prompt=prompt,
                model=fb_model,
                expect_json=expect_json,
                timeout=timeout,
                task=task,
                agent=agent,
            )

            if not fb_result.is_error and fb_result.text:
                fb_result.fallback_used = True
                fb_result.fallback_reason = (
                    f"Primary provider '{self.primary_name}' failed ({primary_error}); "
                    f"routed to '{fb_name}' ({fb_model})."
                )
                return fb_result

        # All attempts failed; return the primary result with error info
        return primary_result

    async def get_all_providers_status(self) -> list[dict[str, Any]]:
        """Return a status summary of all supported providers for the dashboard/CLI."""
        results = []
        for name, cls in PROVIDER_CLASSES.items():
            provider = self.get_provider(name)
            preflight_msg = await provider.preflight()
            is_ready = not bool(preflight_msg)
            models = [
                {
                    "id": m.id,
                    "name": m.name,
                    "free": m.is_free,
                    "context_window": m.context_window,
                    "best_for": m.best_for,
                    "description": m.description,
                }
                for m in ModelRegistry.get_models_for_provider(name)
            ]
            results.append({
                "name": name,
                "label": provider.label,
                "is_primary": name == self.primary_name,
                "is_free": provider.is_free,
                "is_paid": provider.is_paid,
                "requires_key": provider.requires_key,
                "ready": is_ready,
                "status_message": preflight_msg or "Ready",
                "default_model": provider.default_model,
                "selected_model": self.configured_model if name == self.primary_name else provider.default_model,
                "models": models,
            })
        return results

