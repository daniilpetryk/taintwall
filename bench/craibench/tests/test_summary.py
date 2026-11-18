# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Summaries that accumulate over separate runs."""

import json
from pathlib import Path
from typing import Any

from craibench_runner.summary import merged


def meter(calls: int, cost: float) -> dict[str, Any]:
    return {"calls": calls, "cache_hits": 0, "cost": cost}


def summary(suites: dict[str, Any], calls: int, cost: float) -> dict[str, Any]:
    return {"model": "m", "suites": suites, "meter": meter(calls, cost)}


def test_first_run_is_kept_as_is(tmp_path: Path) -> None:
    out = merged(tmp_path / "s.json", summary({"chain": 1}, 3, 0.5))
    assert out["suites"] == {"chain": 1}
    assert out["meter"]["cost"] == 0.5
    assert len(out["runs"]) == 1


def test_later_runs_add_suites_and_spend(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text(json.dumps(merged(path, summary({"chain": 1}, 3, 0.5))), encoding="utf-8")
    out = merged(path, summary({"dao_nft": 2}, 2, 0.25))
    assert out["suites"] == {"chain": 1, "dao_nft": 2}
    assert (out["meter"]["calls"], out["meter"]["cost"]) == (5, 0.75)


def test_cached_reruns_add_no_spend(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text(json.dumps(merged(path, summary({"chain": 1}, 3, 0.5))), encoding="utf-8")
    out = merged(path, summary({"chain": 1}, 0, 0.0))
    assert len(out["runs"]) == 1
    assert out["meter"]["cost"] == 0.5
