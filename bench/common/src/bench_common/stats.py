# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Rates with confidence intervals, and deterministic sampling of task pairs."""

import hashlib
import math
from collections.abc import Hashable, Iterable, Mapping, Sequence

Z_95 = 1.959963984540054
"""Two-sided 95% normal quantile."""


def wilson(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial rate; ``(0, 1)`` when ``total`` is 0."""
    if total == 0:
        return 0.0, 1.0
    rate = successes / total
    denominator = 1 + z * z / total
    centre = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def rate_summary[K: Hashable](results: Mapping[K, bool]) -> dict[str, float | int | None]:
    """Rate, count and 95% Wilson interval of boolean results."""
    total = len(results)
    successes = sum(results.values())
    low, high = wilson(successes, total)
    return {
        "rate": successes / total if total else None,
        "successes": successes,
        "total": total,
        "ci95_low": low,
        "ci95_high": high,
    }


def sample(items: Iterable[str], size: int | None, seed: str) -> list[str]:
    """A deterministic sample: the ``size`` items with the smallest seeded hash.

    The same seed always picks the same items, and a larger sample contains a
    smaller one, so runs can grow without changing earlier picks.
    """
    ranked = sorted(items, key=lambda item: hashlib.sha256(f"{seed}:{item}".encode()).hexdigest())
    return ranked if size is None else ranked[:size]


def pairs_of(mapping: Mapping[str, Sequence[str]]) -> list[str]:
    """``user_task:injection_task`` strings from a map of valid pairs."""
    return [
        f"{user}:{injection}" for user, injections in mapping.items() for injection in injections
    ]
