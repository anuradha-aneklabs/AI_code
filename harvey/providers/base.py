"""Base provider interfaces and result models for Harvey AI Multi-Provider system."""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ProviderResult:
    """The result returned by any AI provider call."""

    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    is_error: bool = False
    error_message: str = ""
    fallback_used: bool = False
    fallback_reason: str = ""
    task: str = ""
    agent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class PaidProviderNotApprovedError(RuntimeError):
    """Raised when a paid provider (such as xAI) is called without explicit operator approval."""


class ProviderUnavailableError(RuntimeError):
    """Raised when an AI provider is unreachable, misconfigured, or over quota."""


class AIProvider(abc.ABC):
    """Abstract base class for Harvey AI Providers."""

    name: str = "base"
    label: str = "Base Provider"
    is_free: bool = True
    is_paid: bool = False
    requires_key: bool = False
    env_key: str = ""
    default_model: str = ""

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        self.config = config or {}
        self.env = env or {}

    @abc.abstractmethod
    async def preflight(self) -> str:
        """Check whether this provider is ready to be used.

        Returns an empty string "" if ready, or a human-readable explanation
        of what is missing or failing.
        """

    @abc.abstractmethod
    async def generate(
        self,
        prompt: str,
        model: str = "",
        expect_json: bool = False,
        timeout: float = 60.0,
        task: str = "",
        agent: str = "",
    ) -> ProviderResult:
        """Send a prompt to the model and return a ProviderResult."""

    def get_api_key(self) -> str:
        """Retrieve the configured API key if required, without logging or exposing it."""
        if not self.env_key:
            return ""
        return (self.env.get(self.env_key) or "").strip()

