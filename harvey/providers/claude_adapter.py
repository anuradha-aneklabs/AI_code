"""Claude provider adapter preserving existing Claude Code CLI headless integration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from harvey.providers.base import AIProvider, ProviderResult

logger = logging.getLogger("harvey.providers.claude")


class ClaudeProvider(AIProvider):
    """Adapter for Claude Pro/Max subscription via headless Claude Code CLI."""

    name = "claude"
    label = "Claude"
    is_free = False
    is_paid = False  # Covered by flat Claude subscription
    requires_key = False
    default_model = "claude-3-5-sonnet-latest"

    def __init__(self, config: Optional[dict[str, Any]] = None, env: Optional[dict[str, str]] = None):
        super().__init__(config, env)

    async def preflight(self) -> str:
        """Verify Claude CLI isolation capabilities."""
        from harvey.claude_runtime import ClaudeRuntimeError, check_cli

        try:
            await asyncio.to_thread(check_cli)
            return ""
        except ClaudeRuntimeError as e:
            return str(e)
        except Exception as e:
            return f"Claude CLI check failed: {e}"

    async def generate(
        self,
        prompt: str,
        model: str = "",
        expect_json: bool = False,
        timeout: float = 300.0,
        task: str = "",
        agent: str = "",
    ) -> ProviderResult:
        """Execute headless Claude Code CLI turn."""
        from harvey import claude_runtime

        target_model = model or self.config.get("model", "")
        start_time = time.monotonic()

        try:
            executable = await asyncio.to_thread(claude_runtime.check_cli)
        except Exception as e:
            return ProviderResult(
                text="",
                provider=self.name,
                model=target_model,
                is_error=True,
                error_message=str(e),
                task=task,
                agent=agent,
            )

        cmd = claude_runtime.build_command(executable, target_model, None)

        try:
            with claude_runtime.isolated_cwd() as cwd:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=cwd,
                    env=claude_runtime.runtime_env(cwd),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")),
                    timeout=timeout,
                )

            duration_ms = int((time.monotonic() - start_time) * 1000)

            if process.returncode != 0:
                err = stderr.decode(errors="replace").strip()
                return ProviderResult(
                    text="",
                    provider=self.name,
                    model=target_model,
                    duration_ms=duration_ms,
                    is_error=True,
                    error_message=err,
                    task=task,
                    agent=agent,
                )

            raw = stdout.decode(errors="replace").strip()
            # Parse payload if JSON output
            text = raw
            input_tokens = 0
            output_tokens = 0
            cost_usd = 0.0

            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and payload.get("type") == "result":
                    text = str(payload.get("result") or "")
                    usage = payload.get("usage") or {}
                    input_tokens = int(usage.get("input_tokens") or 0)
                    output_tokens = int(usage.get("output_tokens") or 0)
                    cost_usd = float(payload.get("total_cost_usd") or 0.0)
            except Exception:
                pass

            return ProviderResult(
                text=text,
                provider=self.name,
                model=target_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
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

