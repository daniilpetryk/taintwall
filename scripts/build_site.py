# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Build the GitHub Pages site: the architecture diagrams and the measurements page.

The measurements page reads like patch notes: one card per patch from
``bench/results/journal.json``, newest first, with what changed, what was
measured, and how each number moved since it was last measured (better,
worse, or within noise when the 95% intervals overlap). Values are never
copied into the journal: each measurement points at a committed result file
and is read from it here, so the page cannot disagree with the data.

Standard library only, so the Pages workflow needs no environment::

    python3 scripts/build_site.py _site
"""

import dataclasses
import html
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "bench" / "results" / "journal.json"
REPO_URL = "https://github.com/daniilpetryk/taintwall"
Z_95 = 1.959963984540054
"""Two-sided 95% normal quantile, as in ``bench_common.stats``."""
SERIES_SLOTS = 8
"""Categorical colors of the reference palette, assigned in fixed order."""
DASH = "&ndash;"

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


def _e(text: object) -> str:
    return html.escape(str(text))


def _pct(value: float) -> str:
    return f"{100 * value:.0f}%"


def _model(series: str) -> str:
    """The model a series is about: what follows the last ``·``, or the series itself."""
    return series.rsplit("·", 1)[-1].strip()


def slot(series: str, order: list[str]) -> int:
    """The color slot of a series: its model's place in the journal's fixed order."""
    model = _model(series)
    return (order.index(model) if model in order else len(order)) % SERIES_SLOTS


CHANGE_TEXT = {
    "first": ("", "first measurement", "neutral"),
    "noise": ("≈", "within noise", "neutral"),
    "better": ("▲", "better", "good"),
    "worse": ("▼", "worse", "critical"),
}


def change_chip(change: Change) -> str:
    """The change cell: icon, label and delta, never color alone."""
    icon, label, tone = CHANGE_TEXT[change.kind]
    if change.kind == "first":
        return f'<span class="chip {tone}">{label}</span>'
    delta = f"{100 * change.delta:+.0f} pp"
    against = f' <span class="muted">vs {_e(change.against)}</span>'
    chip = f'<span aria-hidden="true">{icon}</span> {label}, {delta}'
    return f'<span class="chip {tone}">{chip}</span>{against}'


def rate_cells(rate: Rate) -> str:
    """Value and interval cells of a table row."""
    count = f'<span class="muted">({rate.successes}/{rate.total})</span>'
    interval = f"{_pct(rate.low)}{DASH}{_pct(rate.high)}"
    return f'<td class="num">{_pct(rate.value)} {count}</td><td class="num muted">{interval}</td>'


@dataclasses.dataclass(frozen=True)
class Frame:
    """Geometry of one small chart: columns are the patches that measured the metric."""

    columns: list[int]
    width: int = 320
    height: int = 168
    left: int = 36
    right: int = 12
    top: int = 14
    bottom: int = 28

    @property
    def plot_height(self) -> int:
        """Height of the plotting area."""
        return self.height - self.top - self.bottom

    def x(self, patch: int, series_slot: int) -> float:
        """Centre of a patch's column, nudged per series so whiskers stay apart."""
        step = (self.width - self.left - self.right) / max(len(self.columns), 1)
        return self.left + step * (self.columns.index(patch) + 0.5) + (series_slot - 2) * 6

    def y(self, value: float) -> float:
        """Vertical position of a rate."""
        return self.top + self.plot_height * (1 - value)


def axes(frame: Frame, patches: list[Json]) -> str:
    """Recessive grid lines, percent ticks, and the PR of each column."""
    parts = []
    for tick in (0, 0.5, 1):
        y = f"{frame.y(tick):.1f}"
        parts.append(f'<line class="grid" x1="{frame.left}" x2="{frame.width - frame.right}"')
        parts.append(f' y1="{y}" y2="{y}"/>')
        parts.append(f'<text class="axis" x="{frame.left - 6}" y="{frame.y(tick) + 4:.1f}"')
        parts.append(f' text-anchor="end">{_pct(tick)}</text>')
    for patch in frame.columns:
        x = frame.x(patch, 2)
        pr = patches[patch]["prs"][-1]
        parts.append(f'<text class="axis" x="{x:.1f}" y="{frame.height - 8}" text-anchor="middle">')
        parts.append(f"#{_e(pr)}</text>")
    return "".join(parts)


def mark(frame: Frame, point: Point, series_slot: int) -> str:
    """A point with its interval whisker, a larger hit target and a tooltip."""
    rate, x = point.rate, frame.x(point.patch, series_slot)
    interval = f"95% CI {_pct(rate.low)}-{_pct(rate.high)}"
    tip = f"{point.key[1]}: {_pct(rate.value)} ({rate.successes}/{rate.total}), {interval}"
    return (
        f'<g class="mark s{series_slot}" tabindex="0" data-tip="{_e(tip)}">'
        f'<line class="whisker" x1="{x:.1f}" x2="{x:.1f}" '
        f'y1="{frame.y(rate.high):.1f}" y2="{frame.y(rate.low):.1f}"/>'
        f'<circle cx="{x:.1f}" cy="{frame.y(rate.value):.1f}" r="4"/>'
        f'<rect class="hit" x="{x - 9:.1f}" y="{frame.top}" width="18" '
        f'height="{frame.plot_height}"/></g>'
    )


def chart(metric: str, timeline: Timeline, order: list[str]) -> str:
    """One small-multiple chart: rate per patch, a line per series, interval whiskers."""
    spec = timeline.journal["metrics"][metric]
    own = [point for point in timeline.points if point.key[0] == metric]
    frame = Frame(sorted({point.patch for point in own}))
    by_series: dict[str, list[Point]] = {}
    for point in own:
        by_series.setdefault(point.key[1], []).append(point)
    marks = []
    for series, series_points in by_series.items():
        series_slot = slot(series, order)
        if len(series_points) > 1:
            path = " ".join(
                f"{frame.x(p.patch, series_slot):.1f},{frame.y(p.rate.value):.1f}"
                for p in series_points
            )
            marks.append(f'<polyline class="line s{series_slot}" points="{path}"/>')
        marks.extend(mark(frame, point, series_slot) for point in series_points)
    svg = (
        f'<svg viewBox="0 0 {frame.width} {frame.height}" role="img" '
        f'aria-label="{_e(spec["label"])} per patch">'
        f"{axes(frame, timeline.journal['patches'])}{''.join(marks)}</svg>"
    )
    better = "higher is better" if spec["better"] == "higher" else "lower is better"
    caption = f'{_e(spec["label"])} <span class="muted">· {better}</span>'
    return f'<figure class="chart"><figcaption>{caption}</figcaption>{svg}</figure>'


def legend(series: list[str], order: list[str]) -> str:
    """Series names beside their colors; identity is never color alone."""
    items = "".join(
        f'<li><span class="swatch s{slot(name, order)}"></span>{_e(name)}</li>' for name in series
    )
    return f'<ul class="legend">{items}</ul>'


def card_head(patch: Json) -> str:
    """Kind, date, stage, PRs and commit, then the title and what changed."""
    prs = ", ".join(f'<a href="{REPO_URL}/pull/{pr}">#{pr}</a>' for pr in patch["prs"])
    commit = (
        f'<a href="{REPO_URL}/commit/{_e(patch["commit"])}"><code>{_e(patch["commit"])}</code></a>'
    )
    meta = f"{_e(patch['date'])} · stage {_e(patch['stage'])} · {prs} · {commit}"
    badge = f'<span class="badge">{_e(patch["kind"])}</span>'
    return (
        f'<header>{badge}<span class="muted">{meta}</span></header>'
        f"<h3>{_e(patch['title'])}</h3><p>{_e(patch['changed'])}</p>"
    )


def card_table(patch: Json, timeline: Timeline) -> str:
    """The measurements of a patch with their values, intervals and changes."""
    rows = []
    for measurement in patch["measurements"]:
        spec = timeline.journal["metrics"][measurement["metric"]]
        point = timeline.by_id[measurement["id"]]
        chip = change_chip(timeline.changes[measurement["id"]])
        rows.append(
            f"<tr><td>{_e(spec['group'])}: {_e(spec['label'])}</td>"
            f"<td>{_e(measurement['series'])}</td>{rate_cells(point.rate)}<td>{chip}</td></tr>"
        )
    head = (
        "<thead><tr><th>Metric</th><th>System</th><th class=num>Value</th>"
        "<th class=num>95% CI</th><th>Change</th></tr></thead>"
    )
    return f'<div class="scroll"><table>{head}<tbody>{"".join(rows)}</tbody></table></div>'


def patch_card(patch: Json, timeline: Timeline) -> str:
    """One patch note: what changed, what was measured, how the numbers moved."""
    head = card_head(patch)
    if not patch["measurements"]:
        why = f"No new measurement. {_e(patch.get('why', ''))}"
        return f'<article class="patch compact">{head}<p class="muted">{why}</p></article>'
    notes = "".join(
        f"<p><strong>{label}.</strong> {_e(patch[key])}</p>"
        for key, label in (("question", "Question"), ("result", "Result"), ("decision", "Decision"))
        if key in patch
    )
    extra = [f"cost ${patch['cost_usd']:.2f}"] if "cost_usd" in patch else []
    if "report" in patch:
        extra.append(f'<a href="{REPO_URL}/blob/main/{_e(patch["report"])}">report</a>')
    footer = f'<p class="muted">{" · ".join(extra)}</p>'
    return f'<article class="patch">{head}{notes}{card_table(patch, timeline)}{footer}</article>'


def scoreboard(timeline: Timeline) -> str:
    """The latest value of every metric and system, with its last change."""
    latest: dict[tuple[str, str], Point] = {}
    for point in timeline.points:
        latest[point.key] = point
    rows = []
    for (metric, series), point in sorted(latest.items(), key=lambda item: item[1].patch):
        spec = timeline.journal["metrics"][metric]
        pr = timeline.journal["patches"][point.patch]["prs"][-1]
        chip = change_chip(timeline.changes[point.measurement["id"]])
        rows.append(
            f"<tr><td>{_e(spec['group'])}</td><td>{_e(spec['label'])}</td><td>{_e(series)}</td>"
            f'{rate_cells(point.rate)}<td><a href="{REPO_URL}/pull/{pr}">#{pr}</a></td>'
            f"<td>{chip}</td></tr>"
        )
    head = (
        "<thead><tr><th>Benchmark</th><th>Metric</th><th>System</th><th class=num>Value</th>"
        "<th class=num>95% CI</th><th>Measured in</th><th>Last change</th></tr></thead>"
    )
    return f'<div class="scroll"><table>{head}<tbody>{"".join(rows)}</tbody></table></div>'


def trends(timeline: Timeline, order: list[str]) -> str:
    """Charts for metrics measured at least twice; one point says nothing about a trend."""
    groups: dict[str, list[str]] = {}
    for metric, spec in timeline.journal["metrics"].items():
        own = [point for point in timeline.points if point.key[0] == metric]
        if len(own) > len({point.key for point in own}):
            groups.setdefault(spec["group"], []).append(metric)
    if not groups:
        return (
            '<p class="muted">No metric has been measured twice yet. A chart appears here '
            "as soon as a later patch measures a metric again.</p>"
        )
    sections = []
    for group, metrics in groups.items():
        names = {point.key[1] for point in timeline.points if point.key[0] in metrics}
        series = sorted(names, key=lambda name: slot(name, order))
        charts = "".join(chart(metric, timeline, order) for metric in metrics)
        sections.append(
            f'<section class="group"><h3>{_e(group)}</h3>{legend(series, order)}'
            f'<div class="charts">{charts}</div></section>'
        )
    hint = (
        '<p class="muted">One point per patch that measured it; whiskers are 95% Wilson '
        "intervals. Hover or focus a point for its numbers.</p>"
    )
    return hint + "".join(sections)


STYLE_PATH = Path(__file__).with_name("site.css")


def nav(active: str) -> str:
    """The site's top bar, shared by both pages."""
    links = (
        ("index.html", "Architecture"),
        ("measurements.html", "Measurements"),
        (REPO_URL, "Repository"),
    )
    current = ' aria-current="page"'
    items = "".join(
        f'<a href="{href}"{current if href == active else ""}>{label}</a>' for href, label in links
    )
    return f'<nav class="site"><strong>taintwall</strong>{items}</nav>'


def document(title: str, body: str, style: str) -> str:
    """A complete HTML page."""
    head = (
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"<title>{_e(title)}</title>\n<style>{style}</style>"
    )
    shell = f'<!doctype html>\n<html lang="en">\n<head>\n{head}\n</head>\n'
    return f"{shell}<body>\n{body}\n</body>\n</html>\n"


TOOLTIP = """
<div id="tip" role="tooltip"></div>
<script>
const tip = document.getElementById("tip");
function show(event, target) {
  tip.textContent = target.dataset.tip;
  tip.style.display = "block";
  const box = target.getBoundingClientRect();
  const x = event ? event.clientX : box.left + box.width / 2;
  const y = event ? event.clientY : box.top;
  tip.style.left = Math.min(x + 12, innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = Math.max(y - tip.offsetHeight - 10, 8) + "px";
}
for (const m of document.querySelectorAll(".mark")) {
  m.addEventListener("pointermove", (event) => show(event, m));
  m.addEventListener("focus", () => show(null, m));
  m.addEventListener("pointerleave", () => { tip.style.display = "none"; });
  m.addEventListener("blur", () => { tip.style.display = "none"; });
}
</script>
"""


def measurements_page(journal: Json) -> str:
    """The patch-notes page for every measurement in the journal."""
    timeline = Timeline.of(journal)
    order: list[str] = journal["series_colors"]
    cards = "".join(patch_card(patch, timeline) for patch in reversed(journal["patches"]))
    spent = sum(float(patch.get("cost_usd", 0)) for patch in journal["patches"])
    lede = (
        '<p class="lede">Patch notes for what we measure, newest first: what changed, what '
        "was measured, and how each number moved since it was last measured. A change counts "
        "as better or worse only when the 95% intervals do not overlap; otherwise it is within "
        f"noise. Spent on model calls so far: ${spent:.2f}.</p>"
        '<p class="lede"><a href="#now">Current numbers</a> · <a href="#trends">Trends</a></p>'
    )
    body = (
        f'{nav("measurements.html")}<main class="wrap"><h1>Measurements</h1>{lede}'
        f"<h2>Patch notes</h2>{cards}"
        f'<h2 id="now">Current numbers</h2>{scoreboard(timeline)}'
        f'<h2 id="trends">Trends</h2>{trends(timeline, order)}</main>{TOOLTIP}'
    )
    return document("taintwall measurements", body, STYLE_PATH.read_text(encoding="utf-8"))


def architecture_page(content: str) -> str:
    """The diagrams page: ``docs/architecture.html`` is content without a shell."""
    bar = (
        "nav.site{display:flex;gap:16px;padding:12px 16px;font:15px system-ui,sans-serif;"
        "border-bottom:1px solid #8884}"
    )
    return document("taintwall architecture", nav("index.html") + content, bar)


def main() -> int:
    """Write ``index.html`` and ``measurements.html`` into the given directory."""
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    journal = json.loads(JOURNAL.read_text(encoding="utf-8"))
    (out / "measurements.html").write_text(measurements_page(journal), encoding="utf-8")
    content = (ROOT / "docs" / "architecture.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(architecture_page(content), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
