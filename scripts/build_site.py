# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Build the GitHub Pages site: the architecture diagrams and the measurements page.

The measurements page reads like patch notes: where the project stands, how
to read the numbers, then one card per patch from ``bench/results/journal.json``
(newest first) with what changed, what was measured, and how each number moved
since it was last measured. The data model is ``measurement_journal``.

Standard library only, so the Pages workflow needs no environment::

    python3 scripts/build_site.py _site
"""

import dataclasses
import html
import re
import sys
from pathlib import Path

from measurement_journal import ROOT, Change, Json, Point, Timeline, load

REPO_URL = "https://github.com/daniilpetryk/taintwall"
SERIES_SLOTS = 8
"""Categorical colors of the reference palette, assigned in fixed order."""
DASH = "&ndash;"
_CODE = re.compile(r"`([^`]+)`")


def _e(text: object) -> str:
    return html.escape(str(text))


def _text(text: object) -> str:
    """Escaped prose with `inline code` as code."""
    return _CODE.sub(r"<code>\1</code>", _e(text))


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
    "noise": ("≈", "within noise", "neutral"),
    "better": ("▲", "better", "good"),
    "worse": ("▼", "worse", "critical"),
}


def change_chip(change: Change) -> str:
    """Icon, label and delta of a change, never color alone; nothing for a first measurement."""
    if change.kind == "first":
        return ""
    icon, label, tone = CHANGE_TEXT[change.kind]
    delta = f"{100 * change.delta:+.0f} pp"
    chip = f'<span aria-hidden="true">{icon}</span> {label}, {delta}'
    return (
        f'<span class="chip {tone}">{chip}</span>'
        f'<span class="muted vs">vs {_e(change.against)}</span>'
    )


def value_cell(point: Point, change: Change, note: str = "") -> str:
    """A pivot cell: the rate, its count and interval, and its change if any."""
    rate = point.rate
    count = f'<span class="muted">{rate.successes}/{rate.total}</span>'
    interval = f'<span class="muted ci">{_pct(rate.low)}{DASH}{_pct(rate.high)}</span>'
    chip = change_chip(change) or '<span class="muted">&mdash;</span>'
    return (
        f'<td class="cell"><span class="value">{_pct(rate.value)}</span> {count}'
        f"{interval}{note}<span class=change>{chip}</span></td>"
    )


def metric_head(spec: Json) -> str:
    """A column head: the metric, its direction, and its meaning on hover."""
    arrow = "↑ higher is better" if spec["better"] == "higher" else "↓ lower is better"
    help_text = _e(spec.get("help", ""))
    return (
        f'<th scope="col"><span class="term" title="{help_text}">{_e(spec["label"])}</span>'
        f'<span class="direction">{arrow}</span></th>'
    )


@dataclasses.dataclass(frozen=True)
class Grid:
    """One benchmark's measurements as a table: systems in rows, metrics in columns."""

    group: str
    metrics: list[str]
    rows: dict[str, dict[str, Point]]


def grids(points: list[Point], journal: Json, order: list[str]) -> list[Grid]:
    """Group points by benchmark, keeping metric and system order stable."""
    by_group: dict[str, Grid] = {}
    for point in points:
        metric, series = point.key
        group = journal["metrics"][metric]["group"]
        grid = by_group.setdefault(group, Grid(group, [], {}))
        if metric not in grid.metrics:
            grid.metrics.append(metric)
        grid.rows.setdefault(series, {})[metric] = point
    for grid in by_group.values():
        ordered = sorted(grid.rows.items(), key=lambda item: slot(item[0], order))
        grid.rows.clear()
        grid.rows.update(ordered)
    return list(by_group.values())


def grid_table(grid: Grid, timeline: Timeline, journal: Json, *, with_patch: bool = False) -> str:
    """A benchmark's table; with ``with_patch`` each cell names the PR that measured it."""
    heads = "".join(metric_head(journal["metrics"][metric]) for metric in grid.metrics)
    rows = []
    for series, cells in grid.rows.items():
        parts = [f'<th scope="row">{_e(series)}</th>']
        for metric in grid.metrics:
            point = cells.get(metric)
            if point is None:
                parts.append('<td class="cell muted">&mdash;</td>')
                continue
            note = _measured_in(journal, point) if with_patch else ""
            parts.append(value_cell(point, timeline.changes[point.measurement["id"]], note))
        rows.append(f"<tr>{''.join(parts)}</tr>")
    head = f'<thead><tr><th scope="col">System</th>{heads}</tr></thead>'
    caption = f"<caption>{_e(grid.group)}</caption>"
    table = f'<table class="grid-table">{caption}{head}<tbody>{"".join(rows)}</tbody></table>'
    return f'<div class="scroll">{table}</div>'


def _measured_in(journal: Json, point: Point) -> str:
    """A link from a current number to the patch card that measured it."""
    patch = journal["patches"][point.patch]
    link = f'<a href="#{_e(patch["id"])}">#{patch["prs"][-1]}</a>'
    return f'<span class="muted ci">measured in {link}</span>'


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


def stage_name(journal: Json, stage: object) -> str:
    """``stage 1 · Core: labels and interpreter``."""
    name = journal.get("stages", {}).get(str(stage))
    return f"stage {_e(stage)} · {_e(name)}" if name else f"stage {_e(stage)}"


def card_head(patch: Json, journal: Json) -> str:
    """Kind, date, stage, PRs and commit, then the title (a link to itself) and what changed."""
    prs = ", ".join(f'<a href="{REPO_URL}/pull/{pr}">#{pr}</a>' for pr in patch["prs"])
    commit = (
        f'<a href="{REPO_URL}/commit/{_e(patch["commit"])}"><code>{_e(patch["commit"])}</code></a>'
    )
    meta = f"{_e(patch['date'])} · {stage_name(journal, patch['stage'])} · {prs} · {commit}"
    badge = f'<span class="badge">{_e(patch["kind"])}</span>'
    title = f'<a class="self" href="#{_e(patch["id"])}">{_e(patch["title"])}</a>'
    return (
        f'<header>{badge}<span class="muted">{meta}</span></header>'
        f"<h3>{title}</h3><p>{_text(patch['changed'])}</p>"
    )


def patch_card(patch: Json, timeline: Timeline, order: list[str]) -> str:
    """One patch note: what changed, what was measured, how the numbers moved."""
    journal = timeline.journal
    head = card_head(patch, journal)
    anchor = f'id="{_e(patch["id"])}"'
    if not patch["measurements"]:
        why = f"No new measurement. {_text(patch.get('why', ''))}"
        return f'<article class="patch compact" {anchor}>{head}<p class="muted">{why}</p></article>'
    notes = "".join(
        f"<p><strong>{label}.</strong> {_text(patch[key])}</p>"
        for key, label in (("question", "Question"), ("result", "Result"), ("decision", "Decision"))
        if key in patch
    )
    own = [timeline.by_id[m["id"]] for m in patch["measurements"]]
    tables = "".join(grid_table(grid, timeline, journal) for grid in grids(own, journal, order))
    extra = [f"cost ${patch['cost_usd']:.2f}"] if "cost_usd" in patch else []
    if "report" in patch:
        extra.append(f'<a href="{REPO_URL}/blob/main/{_e(patch["report"])}">full report</a>')
    footer = f'<p class="muted">{" · ".join(extra)}</p>'
    return f'<article class="patch" {anchor}>{head}{notes}{tables}{footer}</article>'


def scoreboard(timeline: Timeline, order: list[str]) -> str:
    """The latest value of every metric and system, one table per benchmark."""
    latest: dict[tuple[str, str], Point] = {}
    for point in timeline.points:
        latest[point.key] = point
    points = sorted(latest.values(), key=lambda point: point.patch)
    tables = grids(points, timeline.journal, order)
    return "".join(grid_table(grid, timeline, timeline.journal, with_patch=True) for grid in tables)


def overview(timeline: Timeline) -> str:
    """What the project is, its goal, where it stands, and the numbers that matter most."""
    view = timeline.journal["overview"]
    tiles = []
    for item in view["highlights"]:
        point = timeline.by_id[item["measurement"]]
        rate, spec = point.rate, timeline.journal["metrics"][point.key[0]]
        tiles.append(
            f'<div class="tile"><span class="big">{_pct(rate.value)}</span>'
            f'<span class="muted ci">95% CI {_pct(rate.low)}{DASH}{_pct(rate.high)} · '
            f"{rate.successes}/{rate.total}</span>"
            f'<span class="tile-label">{_e(spec["group"])}: {_e(spec["label"])}</span>'
            f'<span class="muted">{_text(item["note"])}</span></div>'
        )
    return (
        f'<p class="lede">{_text(view["what"])}</p>'
        f'<p class="lede"><strong>Goal.</strong> {_text(view["goal"])} '
        f"<strong>Now.</strong> {_text(view['now'])}</p>"
        f'<div class="tiles">{"".join(tiles)}</div>'
    )


def glossary(journal: Json) -> str:
    """How to read the page: every term it uses."""
    items = "".join(
        f"<dt>{_e(term)}</dt><dd>{_text(text)}</dd>" for term, text in journal["glossary"]
    )
    summary = "<summary>How to read this page</summary>"
    return f'<details class="glossary" open>{summary}<dl>{items}</dl></details>'


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
    cards = "".join(patch_card(patch, timeline, order) for patch in reversed(journal["patches"]))
    spent = sum(float(patch.get("cost_usd", 0)) for patch in journal["patches"])
    toc = (
        '<p class="toc"><a href="#notes">Patch notes</a> · <a href="#now">Current numbers</a> · '
        f'<a href="#trends">Trends</a> · spent on model calls so far: ${spent:.2f}</p>'
    )
    body = (
        f'{nav("measurements.html")}<main class="wrap"><h1>Measurements</h1>'
        f"{overview(timeline)}{glossary(journal)}{toc}"
        f'<h2 id="notes">Patch notes</h2><p class="muted">Newest first. Each table compares '
        "a number with the last time the same thing was measured; &mdash; means it is the "
        f"first measurement.</p>{cards}"
        f'<h2 id="now">Current numbers</h2>{scoreboard(timeline, order)}'
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
    (out / "measurements.html").write_text(measurements_page(load()), encoding="utf-8")
    content = (ROOT / "docs" / "architecture.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(architecture_page(content), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
