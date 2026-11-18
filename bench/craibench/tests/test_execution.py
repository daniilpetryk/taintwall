# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Resuming from the fork's real result files, without calling any model."""

import json
from pathlib import Path
from types import SimpleNamespace

from craibench_runner.execution import Run

PIPELINE = SimpleNamespace(name="model")


def write(root: Path, relative: str, data: dict[str, object]) -> None:
    path = root / "model" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def run(tmp_path: Path, *, memory: bool = False) -> Run:
    return Run(PIPELINE, tmp_path, memory=memory)


def test_reads_results_where_the_fork_writes_them(tmp_path: Path) -> None:
    # <model>/<system message>/<suite>/<user>/<attack>/<injection>.json
    write(
        tmp_path,
        "default/chain/user_task_0/attack/injection_task_1.json",
        {"utility": True, "security": False},
    )
    assert run(tmp_path).saved("chain", "user_task_0", "attack", "injection_task_1") == (
        True,
        False,
    )


def test_ignores_the_path_the_fork_looks_in(tmp_path: Path) -> None:
    write(
        tmp_path,
        "chain/user_task_0/attack/injection_task_1.json",
        {"utility": True, "security": False},
    )
    assert run(tmp_path).saved("chain", "user_task_0", "attack", "injection_task_1") is None


def test_interrupted_pairs_count_as_unfinished(tmp_path: Path) -> None:
    write(tmp_path, "default/chain/user_task_0/attack/injection_task_1.json", {"messages": []})
    assert run(tmp_path).saved("chain", "user_task_0", "attack", "injection_task_1") is None


def test_memory_results_carry_their_suffix(tmp_path: Path) -> None:
    write(
        tmp_path,
        "default/chain/user_task_0/attack/injection_task_1_memory_injection.json",
        {"utility": False, "security": True},
    )
    assert run(tmp_path).saved("chain", "user_task_0", "attack", "injection_task_1") is None
    assert run(tmp_path, memory=True).saved(
        "chain", "user_task_0", "attack", "injection_task_1"
    ) == (False, True)


def test_utility_results_have_no_suffix_in_memory_runs(tmp_path: Path) -> None:
    write(tmp_path, "default/chain/user_task_0/none/none.json", {"utility": True, "security": True})
    assert run(tmp_path, memory=True).saved("chain", "user_task_0", "none", "none") == (True, True)
