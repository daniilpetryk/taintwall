# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Which user/injection pairs are valid, and the deterministic sample of them.

CrAIBench does not allow every combination: its run scripts list the
injection tasks of each user task (kept in ``pairs.json``). ``crypto_trading``
is the exception and allows every pair.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from bench_common.stats import pairs_of, sample

type Suite = Any
"""The fork's ``TaskSuite``; it ships no type information."""

SAMPLE_SEED = "taintwall-stage0"
"""Fixed seed: the same sample every run, and a larger one contains a smaller one."""

ALL_PAIRS_SUITES = frozenset({"crypto_trading"})
"""Suites whose run script runs every injection task with every user task."""


class UnknownTaskError(ValueError):
    """``pairs.json`` names a task the suite does not have."""

    def __init__(self, suite: str, task: str) -> None:
        super().__init__(f"pairs.json: {suite} has no {task}")


def valid_pairs(suite: Suite, pairs_file: Path) -> dict[str, list[str]]:
    """Injection tasks allowed for each user task of ``suite``, checked against it."""
    if suite.name in ALL_PAIRS_SUITES:
        return {user: sorted(suite.injection_tasks) for user in suite.user_tasks}
    data = json.loads(pairs_file.read_text(encoding="utf-8"))
    pairs: dict[str, list[str]] = dict(data[suite.name])
    for user, injections in pairs.items():
        for task in (user, *injections):
            if task not in suite.user_tasks and task not in suite.injection_tasks:
                raise UnknownTaskError(suite.name, task)
    return pairs


def sampled_pairs(pairs: dict[str, list[str]], size: int | None) -> dict[str, list[str]]:
    """A deterministic sample of ``user:injection`` pairs, grouped by user task."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for pair in sample(pairs_of(pairs), size, SAMPLE_SEED):
        user, injection = pair.split(":")
        grouped[user].append(injection)
    return dict(grouped)
