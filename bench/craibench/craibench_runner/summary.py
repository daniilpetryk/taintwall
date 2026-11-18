# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Run summaries that accumulate over separate runs of the same mode."""

import json
from pathlib import Path
from typing import Any


def merged(path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    """``summary`` with the suites of an earlier run of the same mode kept.

    Suites can be run separately (for example a sampled one after the full
    ones); each run adds its suites instead of replacing the file. ``runs``
    keeps the meter of every run that called the provider, and ``meter`` is
    their total.
    """
    runs = [summary["meter"]]
    suites = summary["suites"]
    if path.exists():
        earlier = json.loads(path.read_text(encoding="utf-8"))
        runs = [*earlier.get("runs", []), *runs]
        suites = earlier.get("suites", {}) | suites
    # Only runs that called the provider; cached reruns add nothing.
    runs = [run for run in runs if run and run.get("calls")]
    total = {key: sum(run[key] for run in runs) for key in summary["meter"]}
    return summary | {"suites": suites, "runs": runs, "meter": total}
