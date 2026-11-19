# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The measurement journal as data: every measurement with its rate and its change.

``bench/results/journal.json`` holds no numbers. Each measurement points at a
committed result file (a benchmark summary or a plan census) and is read from
it here; its change is computed against the measurement it follows (an
explicit ``compare_to``, else the last measurement of the same metric and
system). A change is better or worse only when the 95% intervals do not
overlap; otherwise it is within noise.

Standard library only: the Pages workflow runs it with the runner's Python.
"""

import dataclasses
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "bench" / "results" / "journal.json"
Z_95 = 1.959963984540054
"""Two-sided 95% normal quantile, as in ``bench_common.stats``."""

type Json = dict[str, Any]


@dataclasses.dataclass(frozen=True)
class Rate:
    """A measured rate: successes out of total, with its 95% Wilson interval."""

    successes: int
    total: int
    low: float
    high: float

    @property
    def value(self) -> float:
        """The rate itself."""
        return self.successes / self.total if self.total else 0.0


def wilson(successes: int, total: int) -> tuple[float, float]:
    """Wilson score interval, as ``bench_common.stats.wilson`` (no bench imports here)."""
    if total == 0:
        return 0.0, 1.0
    rate = successes / total
    z2 = Z_95 * Z_95
    denominator = 1 + z2 / total
    centre = (rate + z2 / (2 * total)) / denominator
    margin = Z_95 * math.sqrt(rate * (1 - rate) / total + z2 / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def resolve(source: Json) -> Rate:
    """Read a measurement's rate from the committed result file it points at."""
    if "summary" in source:
        node = json.loads((ROOT / source["summary"]).read_text(encoding="utf-8"))
        for part in source["key"].split("."):
            node = node[part]
        return Rate(node["successes"], node["total"], node["ci95_low"], node["ci95_high"])
    if "census" in source:
        counts = json.loads((ROOT / source["census"]).read_text(encoding="utf-8"))
        row = counts[source["model"]]
        successes, total = row[source["key"]], row["plans"]
        return Rate(successes, total, *wilson(successes, total))
    message = f"unknown source {sorted(source)}"
    raise ValueError(message)


@dataclasses.dataclass(frozen=True)
class Point:
    """One measurement placed on the timeline: the index of its patch and its rate."""

    patch: int
    measurement: Json
    rate: Rate

    @property
    def key(self) -> tuple[str, str]:
        """What a later measurement of the same thing shares: metric and system."""
        return str(self.measurement["metric"]), str(self.measurement["series"])


@dataclasses.dataclass(frozen=True)
class Change:
    """How a measurement compares with the one it follows."""

    kind: str
    """``first``, ``better``, ``worse`` or ``noise``."""
    delta: float = 0.0
    against: str = ""


def compare(rate: Rate, previous: Point | None, better: str, against: str = "") -> Change:
    """Better or worse only when the intervals do not overlap; ``against`` names the reference."""
    if previous is None:
        return Change("first")
    old = previous.rate
    delta = rate.value - old.value
    if rate.low <= old.high and old.low <= rate.high:
        return Change("noise", delta, against)
    improved = (delta > 0) == (better == "higher")
    return Change("better" if improved else "worse", delta, against)


@dataclasses.dataclass
class Timeline:
    """Every measurement of the journal with its rate and its change."""

    journal: Json
    points: list[Point] = dataclasses.field(default_factory=list)
    changes: dict[str, Change] = dataclasses.field(default_factory=dict)
    by_id: dict[str, Point] = dataclasses.field(default_factory=dict)

    @classmethod
    def of(cls, journal: Json) -> "Timeline":
        """Resolve every measurement, in patch order."""
        timeline = cls(journal)
        for index, patch in enumerate(journal["patches"]):
            for measurement in patch["measurements"]:
                timeline.add(Point(index, measurement, resolve(measurement["source"])))
        return timeline

    def add(self, point: Point) -> None:
        """Place ``point`` and work out its change against what it follows."""
        previous = self.previous(point)
        better = self.journal["metrics"][point.key[0]]["better"]
        identifier = str(point.measurement["id"])
        self.changes[identifier] = compare(
            point.rate, previous, better, self.label(point, previous)
        )
        self.points.append(point)
        self.by_id[identifier] = point

    def previous(self, point: Point) -> Point | None:
        """The explicit ``compare_to``, else the last measurement of the same thing."""
        if "compare_to" in point.measurement:
            return self.by_id[point.measurement["compare_to"]]
        earlier = [other for other in self.points if other.key == point.key]
        return earlier[-1] if earlier else None

    def reference(self, previous: Point | None) -> str:
        """Name a comparison: the other metric's label, or the patch measured before."""
        if previous is None:
            return ""
        return f"#{self.journal['patches'][previous.patch]['prs'][-1]}"

    def label(self, point: Point, previous: Point | None) -> str:
        """``vs`` text: another metric is named by its label, the same one by its patch."""
        if previous is not None and previous.key[0] != point.key[0]:
            return str(self.journal["metrics"][previous.key[0]]["label"]).lower()
        return self.reference(previous)


def load() -> Json:
    """The journal as committed."""
    journal: Json = json.loads(JOURNAL.read_text(encoding="utf-8"))
    return journal
