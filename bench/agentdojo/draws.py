# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""How much a run's rates vary from one draw of the models to the next.

A planner samples: two runs of the same prompt write different programs, and
every attacked pair of a task runs the same one, so a run is one draw of 16
programs on banking. ``planner_run --draws N`` runs the suite ``N`` times,
each draw with its own cache entries (``CachedClient(draw=k)``), and
``spread`` gives each rate's value in every draw with their mean, lowest and
highest: the range a single run's number may fall in.
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
"""The rates a spread is given for, by summary block and name."""


def spread(per_draw: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each rate's value in every draw's summary, with their mean, lowest and highest."""
    result: dict[str, dict[str, Any]] = {}
    for block, name in RATES:
        rates = [draw[block][name]["rate"] for draw in per_draw if block in draw]
        if rates:
            result[f"{block}.{name}"] = {
                "rates": rates,
                "mean": fmean(rates),
                "min": min(rates),
                "max": max(rates),
            }
    return result
