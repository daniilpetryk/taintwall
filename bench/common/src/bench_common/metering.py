# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""An OpenAI-compatible client that caches, meters and caps every model call.

Benchmarks call ``client.chat.completions.create``; ``MeteredClient`` stands in
for the client there:

* responses are cached on disk by request hash, so a rerun costs nothing and
  a baseline is computed once (budget rule);
* usage and cost of every uncached call are appended to ``usage.jsonl``;
* a hard ceiling stops the run before it spends more.
"""

import dataclasses
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import openai
from dotenv import dotenv_values
from openai import NOT_GIVEN
from openai.types.chat import ChatCompletion

OPENROUTER = "https://openrouter.ai/api/v1"


class CostCeilingError(RuntimeError):
    """The run would spend more than its ceiling."""

    def __init__(self, spent: float, ceiling: float) -> None:
        super().__init__(f"spent ${spent:.4f}, ceiling ${ceiling:.2f}")


class MissingKeyError(RuntimeError):
    """The repo's ``.env`` has no ``OPENROUTER_API_KEY``."""

    def __init__(self, env_file: Path) -> None:
        super().__init__(f"OPENROUTER_API_KEY is not set in {env_file}")


@dataclasses.dataclass
class Meter:
    """Running totals of one run."""

    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0


def openrouter_client(env_file: Path) -> openai.OpenAI:
    """A client for OpenRouter with the key from ``env_file`` only.

    The shell environment is ignored on purpose: a key exported there may
    belong to something else and lack this project's spend limit.
    """
    api_key = dotenv_values(env_file).get("OPENROUTER_API_KEY")
    if not api_key:
        raise MissingKeyError(env_file)
    return openai.OpenAI(base_url=OPENROUTER, api_key=api_key)


class MeteredClient:
    """Stands in for ``openai.OpenAI`` where a benchmark calls ``chat.completions.create``."""

    def __init__(self, client: openai.OpenAI, run_dir: Path, max_cost: float) -> None:
        self._client = client
        self._cache = run_dir / "cache"
        self._cache.mkdir(parents=True, exist_ok=True)
        self._usage_log = run_dir / "usage.jsonl"
        self._max_cost = max_cost
        self.meter = Meter()

    @property
    def chat(self) -> "MeteredClient":
        """``client.chat``, as in the OpenAI SDK."""
        return self

    @property
    def completions(self) -> "MeteredClient":
        """``client.chat.completions``, as in the OpenAI SDK."""
        return self

    def create(self, **kwargs: Any) -> ChatCompletion:  # noqa: ANN401 - mirrors the OpenAI SDK
        """A cached or metered ``chat.completions.create``."""
        request = {key: value for key, value in kwargs.items() if value is not NOT_GIVEN}
        key = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
        cached = self._cache / f"{key}.json"
        if cached.exists():
            self.meter.cache_hits += 1
            return ChatCompletion.model_validate_json(cached.read_text(encoding="utf-8"))
        if self.meter.cost >= self._max_cost:
            raise CostCeilingError(self.meter.cost, self._max_cost)
        completion: ChatCompletion = self._client.chat.completions.create(
            **request, extra_body={"usage": {"include": True}}
        )
        # Record first: if recording failed after caching, a retry would be
        # served from the cache and the spend would never be counted.
        self._record(completion)
        cached.write_text(completion.model_dump_json(), encoding="utf-8")
        return completion

    def _record(self, completion: ChatCompletion) -> None:
        # Read usage as plain data: SDK versions differ in which fields are
        # typed (CrAIBench pins an old one where prompt_tokens_details and
        # OpenRouter's cost are untyped extras). This must not raise, or the
        # cost ceiling would stop seeing spend.
        usage = completion.usage.model_dump() if completion.usage else {}
        details = usage.get("prompt_tokens_details") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        output = int(usage.get("completion_tokens") or 0)
        cached = int(details.get("cached_tokens") or 0)
        cost = float(usage.get("cost") or 0.0)
        meter = self.meter
        meter.calls += 1
        meter.prompt_tokens += prompt
        meter.completion_tokens += output
        meter.cached_tokens += cached
        meter.cost += cost
        entry = {
            "time": time.time(),
            "model": completion.model,
            "prompt_tokens": prompt,
            "completion_tokens": output,
            "cached_tokens": cached,
            "cost": cost,
        }
        with self._usage_log.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")
