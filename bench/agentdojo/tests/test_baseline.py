# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The AgentDojo runner's pipeline, without calling any model."""

from pathlib import Path
from types import SimpleNamespace

from agentdojo.models import MODEL_NAMES
from baseline import build_pipeline
from bench_common.metering import MeteredClient


def test_pipeline_is_named_after_the_model_and_addresses_it_correctly(tmp_path: Path) -> None:
    client = MeteredClient(SimpleNamespace(), tmp_path, 0.0)  # type: ignore[arg-type]
    pipeline = build_pipeline(client, "openai/gpt-test-model")
    assert pipeline.name == "gpt-test-model"
    assert MODEL_NAMES["gpt-test-model"] == "GPT-4"
