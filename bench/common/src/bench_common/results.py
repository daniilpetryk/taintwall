# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Per-pair results exported from benchmark logs, for committing to the repo.

Benchmark logs (full transcripts) and the response cache stay local. What
the comparison with taintwall needs is the score of every pair, so this
module reads the logs, keeps the fields that identify and score a pair, and
writes them as sorted JSON lines: small, stable under reruns, and readable
in a diff. Log layouts differ between AgentDojo and the CrAIBench fork, so
the fields come from each file's content, not from its path.
"""

import json
from pathlib import Path
from typing import Any

FIELDS = ("suite_name", "user_task_id", "injection_task_id", "attack_type", "memory_injection")
"""What identifies a pair in a log file."""


def finished_pairs(logdir: Path) -> list[dict[str, Any]]:
    """One row per finished pair under ``logdir``; interrupted pairs are skipped."""
    rows = []
    for path in logdir.rglob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "utility" not in data or "security" not in data:
            continue  # written as the task ran, scores missing: interrupted
        row = {field: data.get(field) for field in FIELDS}
        row["memory_injection"] = bool(row["memory_injection"])
        row |= {"utility": bool(data["utility"]), "security": bool(data["security"])}
        rows.append(row)
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def export(logdir: Path, out: Path) -> int:
    """Write the finished pairs under ``logdir`` to ``out`` as JSON lines; return the count."""
    rows = finished_pairs(logdir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    return len(rows)
