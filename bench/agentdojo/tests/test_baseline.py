# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The AgentDojo runner's pipeline, without calling any model."""

from pathlib import Path
from types import SimpleNamespace

from agentdojo.models import MODEL_NAMES
from baseline import Results, build_pipeline, logs, pairs_file, rates
from bench_common.metering import MeteredClient
from draws import BASELINE_RATES, spread


def test_pipeline_is_named_after_the_model_and_addresses_it_correctly(tmp_path: Path) -> None:
    client = MeteredClient(SimpleNamespace(), tmp_path, 0.0)  # type: ignore[arg-type]
    pipeline = build_pipeline(client, "openai/gpt-test-model")
    assert pipeline.name == "gpt-test-model"
    assert MODEL_NAMES["gpt-test-model"] == "GPT-4"


def test_draw_0_keeps_the_paths_of_runs_before_draws(tmp_path: Path) -> None:
    assert (logs(tmp_path, 0), pairs_file(0)) == (tmp_path / "logs", "pairs.jsonl")
    assert (logs(tmp_path, 2), pairs_file(2)) == (tmp_path / "logs-draw-2", "pairs-draw-2.jsonl")


def test_rates_count_every_draws_pairs_and_the_spread_each_draw() -> None:
    first: Results = {
        "utility": {("u0", None): True, ("u1", None): False},
        "asr": {("u0", "i0"): False},
    }
    second: Results = {
        "utility": {("u0", None): True, ("u1", None): True},
        "asr": {("u0", "i0"): True},
    }
    pooled = rates([first, second])
    assert (pooled["utility"]["successes"], pooled["utility"]["total"]) == (3, 4)
    assert (pooled["asr"]["successes"], pooled["asr"]["total"]) == (1, 2)
    per_draw = spread([rates([first]), rates([second])], BASELINE_RATES)
    assert per_draw["utility"] == {"rates": [0.5, 1.0], "mean": 0.75, "min": 0.5, "max": 1.0}
    assert per_draw["asr"]["rates"] == [0.0, 1.0]
    assert "utility_under_attack" not in per_draw
