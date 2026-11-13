"""Model and Provider Registry for Harvey AI Multi-Provider system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ModelInfo:
    """Metadata for an AI model."""

    id: str
    name: str
    provider: str
    is_free: bool
    context_window: int
    best_for: list[str] = field(default_factory=list)
    description: str = ""
    supports_json: bool = True


# Pre-configured catalog of current, valid models across supported providers
MODEL_CATALOG: dict[str, list[ModelInfo]] = {
    "ollama": [
        ModelInfo(
            id="qwen2.5:1.5b",
            name="Qwen 2.5 (1.5B)",
            provider="ollama",
            is_free=True,
            context_window=32768,
            best_for=["routine", "fast", "local"],
            description="Ultra-lightweight fast local model, optimized for low-memory laptop environments.",
        ),
        ModelInfo(
            id="llama3.2",
            name="Llama 3.2 (3B/1B)",
            provider="ollama",
            is_free=True,
            context_window=131072,
            best_for=["routine", "fast", "local"],
            description="Ultra-fast local model, ideal for intent classification and contact scoring.",
        ),
        ModelInfo(
            id="llama3.1",
            name="Llama 3.1 (8B)",
            provider="ollama",
            is_free=True,
            context_window=131072,
            best_for=["routine", "complex", "local"],
            description="Capable 8B open model with strong reasoning for local offline tasks.",
        ),
        ModelInfo(
            id="qwen2.5",
            name="Qwen 2.5 (7B/14B)",
            provider="ollama",
            is_free=True,
            context_window=131072,
            best_for=["complex", "multilingual", "local"],
            description="State-of-the-art open-weights model with exceptional multilingual and reasoning performance.",
        ),
        ModelInfo(
            id="mistral",
            name="Mistral 7B",
            provider="ollama",
            is_free=True,
            context_window=32768,
            best_for=["routine", "local"],
            description="Proven lightweight instruction-following model.",
        ),
    ],
    "gemini": [
        ModelInfo(
            id="gemini-2.0-flash",
            name="Gemini 2.0 Flash",
            provider="gemini",
            is_free=True,
            context_window=1048576,
            best_for=["routine", "complex", "fast", "multimodal"],
            description="Google's next-gen multimodal model with 1M context. Free tier includes up to 15 RPM.",
        ),
        ModelInfo(
            id="gemini-1.5-flash",
            name="Gemini 1.5 Flash",
            provider="gemini",
            is_free=True,
            context_window=1048576,
            best_for=["routine", "fast"],
            description="Fast, reliable workhorse with 1M context window and free tier access.",
        ),
        ModelInfo(
            id="gemini-1.5-pro",
            name="Gemini 1.5 Pro",
            provider="gemini",
            is_free=True,
            context_window=2097152,
            best_for=["complex", "deep-reasoning"],
            description="Advanced reasoning model for complex research and synthesis. Free tier has limited RPM.",
        ),
    ],
    "groq": [
        ModelInfo(
            id="llama-3.3-70b-versatile",
            name="Llama 3.3 70B Versatile",
            provider="groq",
            is_free=True,
            context_window=131072,
            best_for=["complex", "writing", "fast"],
            description="Flagship 70B reasoning and cold email writer on Groq LPU, free tier rate limits.",
        ),
        ModelInfo(
            id="llama-3.1-8b-instant",
            name="Llama 3.1 8B Instant",
            provider="groq",
            is_free=True,
            context_window=131072,
            best_for=["routine", "fast"],
            description="Ultra-fast inference (~1000+ tokens/sec) on Groq, ideal for classification and intent polling.",
        ),
        ModelInfo(
            id="gemma2-9b-it",
            name="Gemma 2 9B IT",
            provider="groq",
            is_free=True,
            context_window=8192,
            best_for=["routine"],
            description="Google Gemma 2 on Groq LPU with free tier limits.",
        ),
    ],
    "openrouter": [
        ModelInfo(
            id="meta-llama/llama-3.3-70b-instruct:free",
            name="Llama 3.3 70B Instruct (Free)",
            provider="openrouter",
            is_free=True,
            context_window=131072,
            best_for=["complex", "writing"],
            description="Community free tier for Llama 3.3 70B via OpenRouter.",
        ),
        ModelInfo(
            id="google/gemini-2.0-flash-exp:free",
            name="Gemini 2.0 Flash Exp (Free)",
            provider="openrouter",
            is_free=True,
            context_window=1048576,
            best_for=["routine", "complex"],
            description="Experimental Gemini 2.0 Flash access via OpenRouter free tier.",
        ),
        ModelInfo(
            id="qwen/qwen-2.5-72b-instruct:free",
            name="Qwen 2.5 72B Instruct (Free)",
            provider="openrouter",
            is_free=True,
            context_window=32768,
            best_for=["complex", "reasoning"],
            description="High-tier open-weights model routed via OpenRouter free tier.",
        ),
        ModelInfo(
            id="deepseek/deepseek-r1:free",
            name="DeepSeek R1 (Free)",
            provider="openrouter",
            is_free=True,
            context_window=65536,
            best_for=["complex", "deep-reasoning"],
            description="Advanced reasoning model accessible on OpenRouter free tier.",
        ),
        ModelInfo(
            id="meta-llama/llama-3.1-8b-instruct:free",
            name="Llama 3.1 8B Instruct (Free)",
            provider="openrouter",
            is_free=True,
            context_window=131072,
            best_for=["routine", "fast"],
            description="Lightweight free-tier model for high-throughput classification.",
        ),
    ],
    "xai": [
        ModelInfo(
            id="grok-2-1212",
            name="Grok 2",
            provider="xai",
            is_free=False,
            context_window=131072,
            best_for=["complex", "reasoning"],
            description="xAI's flagship reasoning model. PAID ONLY — requires approved billing.",
        ),
        ModelInfo(
            id="grok-2-vision-1212",
            name="Grok 2 Vision",
            provider="xai",
            is_free=False,
            context_window=32768,
            best_for=["complex", "multimodal"],
            description="xAI multimodal vision model. PAID ONLY — requires approved billing.",
        ),
    ],
    "claude": [
        ModelInfo(
            id="claude-3-5-sonnet-latest",
            name="Claude 3.5 Sonnet",
            provider="claude",
            is_free=False,
            context_window=200000,
            best_for=["complex", "writing"],
            description="Anthropic Claude Pro/Max subscription model via Claude Code CLI.",
        ),
    ],
    "codex": [
        ModelInfo(
            id="gpt-5.5",
            name="GPT-5.5 / ChatGPT",
            provider="codex",
            is_free=False,
            context_window=128000,
            best_for=["complex"],
            description="OpenAI ChatGPT subscription model via isolated Codex app-server.",
        ),
    ],
}

DEFAULT_MODELS: dict[str, str] = {
    "ollama": "qwen2.5:1.5b",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    "xai": "grok-2-1212",
    "claude": "",
    "codex": "",
}

# Task-based routing profiles:
# Routine tasks require low latency and low complexity:
ROUTINE_TASKS = {"classify_intent", "score_contacts", "summarize", "ping"}
# Complex tasks require creative writing, sequence generation, or in-depth parsing:
COMPLEX_TASKS = {"personal_email", "write_sequence", "generate_response", "trainer"}

TASK_MODELS: dict[str, dict[str, str]] = {
    "groq": {
        "routine": "llama-3.1-8b-instant",
        "complex": "llama-3.3-70b-versatile",
    },
    "gemini": {
        "routine": "gemini-1.5-flash",
        "complex": "gemini-2.0-flash",
    },
    "ollama": {
        "routine": "qwen2.5:1.5b",
        "complex": "qwen2.5:1.5b",
    },
    "openrouter": {
        "routine": "meta-llama/llama-3.1-8b-instruct:free",
        "complex": "meta-llama/llama-3.3-70b-instruct:free",
    },
}


class ModelRegistry:
    """Provides model discovery and task-based routing."""

    @staticmethod
    def get_models_for_provider(provider: str) -> list[ModelInfo]:
        return MODEL_CATALOG.get(provider, [])

    @staticmethod
    def get_default_model(provider: str) -> str:
        return DEFAULT_MODELS.get(provider, "")

    @staticmethod
    def select_model_for_task(provider: str, task: str = "", configured_model: str = "") -> str:
        """Select the best model for a task, respecting an explicit user choice."""
        if configured_model:
            return configured_model
        task_category = "complex" if task in COMPLEX_TASKS else "routine"
        provider_tasks = TASK_MODELS.get(provider, {})
        if task_category in provider_tasks:
            return provider_tasks[task_category]
        return DEFAULT_MODELS.get(provider, "")

    @staticmethod
    def is_free_model(provider: str, model_id: str) -> bool:
        """Check if a specific model is officially marked free."""
        if provider == "xai":
            return False
        if provider == "ollama":
            return True
        for m in MODEL_CATALOG.get(provider, []):
            if m.id == model_id:
                return m.is_free
        # OpenRouter models with :free suffix are free
        if provider == "openrouter" and model_id.endswith(":free"):
            return True
        return False

