# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Per-pair results exported from benchmark logs."""

import json
from pathlib import Path

from bench_common.results import export, finished_pairs


def log(root: Path, name: str, **fields: object) -> None:
    path = root / "deep" / "tree" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"messages": [], **fields}), encoding="utf-8")


def test_keeps_identity_and_scores_only(tmp_path: Path) -> None:
    log(
        tmp_path,
        "a",
        suite_name="chain",
        user_task_id="u1",
        injection_task_id="i1",
        attack_type="x",
        memory_injection=True,
        utility=False,
        security=True,
        duration=3.0,
    )
    assert finished_pairs(tmp_path) == [
        {
            "suite_name": "chain",
            "user_task_id": "u1",
            "injection_task_id": "i1",
            "attack_type": "x",
            "memory_injection": True,
            "utility": False,
            "security": True,
        }
    ]


def test_skips_interrupted_pairs_and_sorts(tmp_path: Path) -> None:
    log(tmp_path, "b", suite_name="chain", user_task_id="u2", utility=True, security=False)
    log(tmp_path, "a", suite_name="chain", user_task_id="u1", utility=True, security=False)
    log(tmp_path, "c", suite_name="chain", user_task_id="u3")
    out = tmp_path / "out" / "pairs.jsonl"
    assert export(tmp_path / "deep", out) == 2
    users = [
        json.loads(line)["user_task_id"] for line in out.read_text(encoding="utf-8").splitlines()
    ]
    assert users == ["u1", "u2"]
    assert all(
        json.loads(line)["memory_injection"] is False for line in out.read_text().splitlines()
    )
