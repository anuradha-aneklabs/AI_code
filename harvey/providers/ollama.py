"""Ollama provider implementation for local offline inference."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from harvey.providers.base import AIProvider, ProviderResult, ProviderUnavailableError

logger = logging.getLogger("harvey.providers.ollama")

DEFAULT_OLLAMA_URL = "http://localhost:11434"


class OllamaProvider(AIProvider):
    """Local, open-weights AI provider via Ollama."""

    name = "ollama"
    label = "Ollama (Local)"
    is_free = True
    is_paid = False
    requires_key = False
    env_key = "OLLAMA_BASE_URL"
    default_model = "qwen2.5:1.5b"

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        super().__init__(config, env)
        base = (
            (self.config.get("ollama_base_url") if self.config else "")
            or (self.env.get("OLLAMA_BASE_URL") if self.env else "")
            or DEFAULT_OLLAMA_URL
        )
        self.base_url = base.rstrip("/")

    async def preflight(self) -> str:
        """Check if local Ollama daemon is reachable and responding."""
        tags_url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(tags_url)
                if res.status_code != 200:
                    return f"Ollama returned HTTP {res.status_code} at {self.base_url}."
                data = res.json()
                models = data.get("models") or []
                if not models:
                    return (
                        f"Ollama is running at {self.base_url}, but no models are downloaded. "
                        f"Run: ollama pull {self.default_model}"
                    )
                return ""
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return (
                f"Ollama is not running at {self.base_url}. Start it with 'ollama serve' "
                f"or download Ollama from https://ollama.com ({type(e).__name__})."
            )
        except Exception as e:
            return f"Ollama check failed: {e}"

    async def list_available_models(self) -> list[str]:
        """Fetch list of pulled model names from Ollama."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                if res.status_code == 200:
                    models = res.json().get("models") or []
                    return [m.get("name", "") for m in models if m.get("name")]
        except Exception:
            pass
        return []

    async def generate(
        self,
        prompt: str,
        model: str = "",
        expect_json: bool = False,
        timeout: float = 120.0,
        task: str = "",
        agent: str = "",
    ) -> ProviderResult:
        """Send prompt to local Ollama API."""
        target_model = model or self.default_model
        url = f"{self.base_url}/api/generate"
        payload: dict[str, Any] = {
            "model": target_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": 1024,
            },
        }
        if expect_json:
            payload["format"] = "json"

        start_time = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(url, json=payload)
                duration_ms = int((time.monotonic() - start_time) * 1000)

                if res.status_code != 200:
                    error_msg = f"Ollama HTTP {res.status_code}: {res.text[:200]}"
                    logger.error(error_msg)
                    return ProviderResult(
                        text="",
                        provider=self.name,
                        model=target_model,
                        duration_ms=duration_ms,
                        is_error=True,
                        error_message=error_msg,
                        task=task,
                        agent=agent,
                    )

                data = res.json()
                text = (data.get("response") or "").strip()
                prompt_tokens = int(data.get("prompt_eval_count") or 0)
                completion_tokens = int(data.get("eval_count") or 0)

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
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            msg = f"Ollama unreachable at {self.base_url} ({type(e).__name__}). Run 'ollama serve'."
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
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            msg = f"Ollama call failed: {e}"
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

