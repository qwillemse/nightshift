"""Text-in, text-out language-model backends for stages that need real reasoning.

Two real backends: the Anthropic API (needs ANTHROPIC_API_KEY) and the `claude` CLI in
print mode (uses whatever login the CLI has). Both are used without tools: the caller
supplies all context in the prompt, which keeps runs cheap and auditable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from nightshift.decider import DeciderError, Transport, urllib_post


class LLMError(RuntimeError):
    pass


# USD per million tokens (input, output). Sonnet from Anthropic's model overview page, Opus from
# a third-party guide; unknown models give no estimate. Check Anthropic's pricing page.
PRICES_PER_MTOK = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}


def estimate_cost(model: str, usage: dict[str, Any]) -> float | None:
    price = PRICES_PER_MTOK.get(model)
    if not price:
        return None
    tokens_in = usage.get("input_tokens", 0)
    tokens_out = usage.get("output_tokens", 0)
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000


class LLM(Protocol):
    last_cost_usd: float | None
    last_usage: dict[str, Any]

    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 4000) -> str: ...


class AnthropicLLM:
    """Anthropic Messages API over plain HTTP."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 180,
        retries: int = 3,
        transport: Transport = urllib_post,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.timeout = timeout
        self.retries = retries
        self.transport = transport
        self.sleep = sleep
        self.last_cost_usd: float | None = None
        self.last_usage: dict[str, Any] = {}

    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 4000) -> str:
        body = json.dumps(
            {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        ).encode()
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        for attempt in range(self.retries + 1):
            try:
                status, payload = self.transport(self.url, headers, body, self.timeout)
            except DeciderError as e:
                raise LLMError(str(e)) from e
            if status in (429, 500, 502, 503, 529) and attempt < self.retries:
                self.sleep(2**attempt)
                continue
            if status != 200:
                raise LLMError(f"Anthropic API returned HTTP {status}: {payload[:300]!r}")
            data = json.loads(payload)
            self.last_usage = data.get("usage", {})
            self.last_cost_usd = estimate_cost(model, self.last_usage)
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        raise LLMError("unreachable")


class ClaudeCLI:
    """`claude -p` in a scratch directory with no tools and no session persistence."""

    def __init__(
        self,
        binary: str | None = None,
        max_budget_usd: float = 0.5,
        timeout: float = 600,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.binary = binary or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        self.max_budget_usd = max_budget_usd
        self.timeout = timeout
        self._run = run
        self.last_cost_usd: float | None = None
        self.last_usage: dict[str, Any] = {}

    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 4000) -> str:
        cmd = [
            self.binary,
            "-p",
            "--model",
            model,
            "--output-format",
            "json",
            "--tools",
            "",
            "--no-session-persistence",
            "--system-prompt",
            system,
            "--max-budget-usd",
            str(self.max_budget_usd),
        ]
        with tempfile.TemporaryDirectory() as scratch:
            try:
                proc = self._run(
                    cmd,
                    input=user,
                    capture_output=True,
                    text=True,
                    cwd=scratch,
                    timeout=self.timeout,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                raise LLMError(f"claude CLI failed to run: {e}") from e
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise LLMError(
                f"claude CLI gave no JSON (exit {proc.returncode}): {proc.stderr[:300]}"
            ) from None
        if data.get("is_error"):
            raise LLMError(f"claude CLI error: {data.get('result')}")
        self.last_cost_usd = data.get("total_cost_usd")
        self.last_usage = data.get("usage") or {}
        return data["result"]


class FakeLLM:
    """Returns a canned reply (or computes one); records every call."""

    def __init__(self, reply: str | Callable[[str, str], str]):
        self._reply = reply
        self.calls: list[tuple[str, str, str]] = []  # (system, user, model)
        self.last_cost_usd: float | None = None
        self.last_usage: dict[str, Any] = {}

    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 4000) -> str:
        self.calls.append((system, user, model))
        return self._reply(system, user) if callable(self._reply) else self._reply
