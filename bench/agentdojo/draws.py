# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""How much a run's rates vary from one draw of the models to the next.

A planner samples: two runs of the same prompt write different programs, and
every attacked pair of a task runs the same one, so a run is one draw of 16
programs on banking. ``planner_run --draws N`` runs the suite ``N`` times,
each draw with its own cache entries (``CachedClient(draw=k)``), and
``spread`` gives each rate's value in every draw with their mean, lowest and
highest: the range a single run's number may fall in. ``baseline --draws N``
does the same for the plain agent (``MeteredClient.draw``).
"""

from collections.abc import Mapping, Sequence
from statistics import fmean
from typing import Any, Final

RATES: Final = (
    ("utility", "utility"),
    ("utility", "plan_failure"),
    ("under_attack", "utility"),
    ("under_attack", "asr"),
    ("under_attack", "plan_failure"),
)
"""The planner's rates a spread is given for, by summary block and name."""

BASELINE_RATES: Final = (("utility",), ("utility_under_attack",), ("asr",))
"""The plain agent's (``baseline.py``), at the top of its summary."""


def spread(
    per_draw: Sequence[Mapping[str, Any]], rates: Sequence[Sequence[str]] = RATES
) -> dict[str, dict[str, Any]]:
    """Each rate's value in every draw's summary, with their mean, lowest and highest.

    A rate is named by its path of keys in a summary; one a draw lacks is left out.
    """
    result: dict[str, dict[str, Any]] = {}
    for path in rates:
        values = [rate for draw in per_draw if (rate := _rate(draw, path)) is not None]
        if values:
            result[".".join(path)] = {
                "rates": values,
                "mean": fmean(values),
                "min": min(values),
                "max": max(values),
            }
    return result


def _rate(summary: Mapping[str, Any], path: Sequence[str]) -> float | None:
    """The rate at ``path`` in ``summary``, or ``None`` if it has none there."""
    node: Any = summary
    for name in path:
        if not isinstance(node, Mapping) or name not in node:
            return None
        node = node[name]
    rate: float | None = node.get("rate")
    return rate
