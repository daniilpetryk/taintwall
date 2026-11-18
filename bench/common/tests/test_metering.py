# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""MeteredClient: caching, cost accounting and the ceiling, without network."""

from pathlib import Path
from typing import Any

import pytest
from bench_common.metering import CostCeilingError, MeteredClient
from bench_common.stats import rate_summary, sample, wilson
from openai.types.chat import ChatCompletion

USAGE = {
    "prompt_tokens": 100,
    "completion_tokens": 10,
    "total_tokens": 110,
    "cost": 0.25,
    "prompt_tokens_details": {"cached_tokens": 40},
}


def completion(usage: dict[str, Any] | None = USAGE) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "test/model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
            "usage": usage,
        }
    )


class FakeOpenAI:
    def __init__(self) -> None:
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, **_kwargs: object) -> ChatCompletion:
        self.calls += 1
        return completion()


def client(tmp_path: Path, ceiling: float = 10.0) -> tuple[MeteredClient, FakeOpenAI]:
    fake = FakeOpenAI()
    return MeteredClient(fake, tmp_path, ceiling), fake  # type: ignore[arg-type]


def test_counts_usage_and_cost(tmp_path: Path) -> None:
    metered, _ = client(tmp_path)
    metered.create(model="m", messages=[])
    meter = metered.meter
    assert (meter.calls, meter.prompt_tokens, meter.completion_tokens) == (1, 100, 10)
    assert (meter.cached_tokens, meter.cost) == (40, 0.25)
    assert (tmp_path / "usage.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_repeats_come_from_the_cache_for_free(tmp_path: Path) -> None:
    metered, fake = client(tmp_path)
    metered.create(model="m", messages=[{"role": "user", "content": "a"}])
    metered.create(model="m", messages=[{"role": "user", "content": "a"}])
    assert (fake.calls, metered.meter.cache_hits, metered.meter.cost) == (1, 1, 0.25)


def test_ceiling_stops_before_spending_more(tmp_path: Path) -> None:
    metered, fake = client(tmp_path, ceiling=0.25)
    metered.create(model="m", messages=[{"role": "user", "content": "a"}])
    with pytest.raises(CostCeilingError):
        metered.create(model="m", messages=[{"role": "user", "content": "b"}])
    assert fake.calls == 1


def test_missing_usage_counts_as_zero(tmp_path: Path) -> None:
    metered, _ = client(tmp_path)
    metered._record(completion(None))  # noqa: SLF001 - the defensive path itself
    assert (metered.meter.calls, metered.meter.cost) == (1, 0.0)


def test_wilson_interval() -> None:
    low, high = wilson(0, 144)
    assert low == 0.0
    assert 0.02 < high < 0.03
    assert wilson(0, 0) == (0.0, 1.0)
    assert rate_summary({1: True, 2: False})["rate"] == 0.5


def test_sample_is_deterministic_and_nested() -> None:
    items = [f"u{i}:i{j}" for i in range(20) for j in range(5)]
    assert sample(items, 10, "s") == sample(items, 10, "s")
    assert sample(items, 10, "s") == sample(items, 30, "s")[:10]
    assert sample(items, None, "s") == sorted(
        sample(items, None, "s"), key=sample(items, None, "s").index
    )
