# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The checker's counts over the study's plans are recomputed, so they cannot drift."""

import json
from pathlib import Path

import pytest
from plan_check import check_run

from taintwall.core.syntax import LANGUAGE_VERSION

PLANS = Path(__file__).resolve().parents[2] / "results" / "agentdojo" / "plans"


@pytest.mark.parametrize("run", sorted(path.name for path in PLANS.iterdir() if path.is_dir()))
def test_the_checker_counts_match_the_plans(run: str) -> None:
    stored = json.loads(
        (PLANS / run / f"check-v{LANGUAGE_VERSION}.json").read_text(encoding="utf-8")
    )
    assert stored == check_run(PLANS / run)


def test_the_checker_finds_real_mistakes_only_where_the_language_accepts() -> None:
    models = check_run(PLANS / "rules")["models"]
    assert isinstance(models, dict)
    for row in models.values():
        assert row["checked"] <= row["accepted"] <= row["plans"]
