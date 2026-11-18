# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Valid pairs and the deterministic sample, without calling any model."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentdojo.task_suite.load_suites import get_suite
from craibench_runner.pairs import UnknownTaskError, sampled_pairs, valid_pairs

PAIRS_FILE = Path(__file__).resolve().parent.parent / "pairs.json"


def suite(name: str, users: int, injections: int) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        user_tasks={f"user_task_{i}": None for i in range(users)},
        injection_tasks={f"injection_task_{i}": None for i in range(injections)},
    )


def test_crypto_trading_allows_every_pair(tmp_path: Path) -> None:
    pairs = valid_pairs(suite("crypto_trading", 3, 2), tmp_path / "unused.json")
    assert sum(len(v) for v in pairs.values()) == 6


def test_pairs_come_from_the_file(tmp_path: Path) -> None:
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps({"chain": {"user_task_0": ["injection_task_1"]}}), encoding="utf-8")
    assert valid_pairs(suite("chain", 1, 2), path) == {"user_task_0": ["injection_task_1"]}


def test_unknown_tasks_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps({"chain": {"user_task_49": ["injection_task_0"]}}), encoding="utf-8")
    with pytest.raises(UnknownTaskError, match="user_task_49"):
        valid_pairs(suite("chain", 40, 2), path)


@pytest.mark.parametrize(("name", "count"), [("chain", 64), ("dao_nft", 155)])
def test_pairs_json_matches_the_pinned_suites(name: str, count: int) -> None:
    # Every task in pairs.json exists in the fork at the pinned commit.
    pairs = valid_pairs(get_suite("v1", name), PAIRS_FILE)
    assert sum(len(v) for v in pairs.values()) == count


def test_sample_is_deterministic_grouped_and_nested() -> None:
    pairs = {f"user_task_{i}": [f"injection_task_{j}" for j in range(5)] for i in range(20)}
    small, large = sampled_pairs(pairs, 10), sampled_pairs(pairs, 40)
    assert small == sampled_pairs(pairs, 10)
    assert sum(len(v) for v in small.values()) == 10
    assert all(set(small[user]) <= set(large[user]) for user in small)
    assert sum(len(v) for v in sampled_pairs(pairs, None).values()) == 100
