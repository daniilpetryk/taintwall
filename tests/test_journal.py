# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The measurement journal and the page built from it.

The journal holds no numbers: each measurement points at a committed result
file, and the page reads the value from there. These tests keep it that way:
every source resolves, every result file is in the journal, the plan census
matches the plans it was computed from, and the page shows every patch.
"""

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from taintwall.core.errors import PlanError
from taintwall.core.plan import parse_plan

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"
KINDS = {"baseline", "study", "change", "defense"}


def _load(name: str) -> ModuleType:
    # The scripts import each other as siblings, as they do when run.
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


measurement_journal = _load("measurement_journal")
build_site = _load("build_site")
JOURNAL = json.loads((RESULTS / "journal.json").read_text(encoding="utf-8"))
MEASUREMENTS = [m for patch in JOURNAL["patches"] for m in patch["measurements"]]


def test_patches_are_complete_and_in_order() -> None:
    dates = [patch["date"] for patch in JOURNAL["patches"]]
    assert dates == sorted(dates)
    ids = [patch["id"] for patch in JOURNAL["patches"]]
    assert len(ids) == len(set(ids))
    for patch in JOURNAL["patches"]:
        assert patch["kind"] in KINDS, patch["id"]
        assert {"title", "changed", "prs", "commit", "stage"} <= set(patch), patch["id"]
        assert all(type(pr) is int for pr in patch["prs"]), patch["id"]
        if patch["measurements"]:
            assert {"question", "result", "decision", "cost_usd"} <= set(patch), patch["id"]
        else:
            assert "why" in patch, patch["id"]


def test_measurements_name_known_metrics_and_earlier_references() -> None:
    seen: set[str] = set()
    for measurement in MEASUREMENTS:
        assert measurement["metric"] in JOURNAL["metrics"], measurement["id"]
        assert measurement["id"] not in seen
        if "compare_to" in measurement:
            assert measurement["compare_to"] in seen, measurement["id"]
        seen.add(measurement["id"])
    for spec in JOURNAL["metrics"].values():
        assert spec["better"] in {"higher", "lower"}


@pytest.mark.parametrize("measurement", MEASUREMENTS, ids=lambda m: m["id"])
def test_every_source_resolves_to_a_rate_with_its_interval(measurement: dict[str, object]) -> None:
    rate = measurement_journal.resolve(measurement["source"])
    assert 0 <= rate.successes <= rate.total
    assert rate.total > 0
    # The page's interval is the benchmark's: same formula as bench_common.stats.
    low, high = measurement_journal.wilson(rate.successes, rate.total)
    assert (low, high) == pytest.approx((rate.low, rate.high))


def test_every_result_file_is_in_the_journal() -> None:
    sources = {m["source"].get("summary") or m["source"].get("census") for m in MEASUREMENTS}
    files = {
        str(path.relative_to(ROOT))
        for pattern in ("**/summary-*.json", "**/census.json")
        for path in RESULTS.glob(pattern)
    }
    assert files - sources == set(), "results not described in bench/results/journal.json"


def _recount(path: Path) -> dict[str, int]:
    plans = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    valid = accepted = 0
    for plan in plans:
        code = plan["code"]
        if code is None:
            continue
        try:
            ast.parse(code)
        except SyntaxError:
            continue
        valid += 1
        try:
            parse_plan(code)
        except PlanError:
            continue
        accepted += 1
    cut = sum(plan.get("finish_reason") == "length" for plan in plans)
    return {"plans": len(plans), "cut": cut, "valid": valid, "accepted": accepted}


@pytest.mark.parametrize(
    "census", sorted(RESULTS.glob("**/census.json")), ids=lambda path: path.parent.name
)
def test_the_census_matches_its_plans(census: Path) -> None:
    # If the plan language changes, rerun `make bench-census` for the run.
    counts = json.loads(census.read_text(encoding="utf-8"))
    for path in sorted(census.parent.glob("*.jsonl")):
        assert counts[path.stem.replace("__", "/")] == _recount(path), path.name


def test_the_page_shows_every_patch_and_how_numbers_moved() -> None:
    page = build_site.measurements_page(JOURNAL)
    for patch in JOURNAL["patches"]:
        assert build_site.html.escape(patch["title"]) in page
    assert "better, +88 pp" in page  # Haiku 5.5: rules in the prompt vs none
    assert "within noise" in page
    for term, _ in JOURNAL["glossary"]:
        assert f"<dt>{build_site.html.escape(term)}</dt>" in page
    for patch in JOURNAL["patches"]:
        assert f'id="{patch["id"]}"' in page  # every card can be linked to
    assert "<code>is None</code>" in page  # inline code in the journal's prose


def test_patches_name_real_releases_and_decisions() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    decisions = {path.name[:4] for path in (ROOT / "docs" / "decisions").glob("[0-9]*.md")}
    for patch in JOURNAL["patches"]:
        assert f"## [{patch['release']}]" in changelog, patch["id"]
        assert set(patch["decisions"]) <= decisions, patch["id"]


def test_highlights_and_stages_name_real_things() -> None:
    ids = {m["id"] for m in MEASUREMENTS}
    assert {item["measurement"] for item in JOURNAL["overview"]["highlights"]} <= ids
    assert {str(patch["stage"]) for patch in JOURNAL["patches"]} <= set(JOURNAL["stages"])
    terms = {term for term, _ in JOURNAL["glossary"]}
    assert {"Utility", "ASR", "95% CI", "pp"} <= terms


Rate = measurement_journal.Rate


@pytest.mark.parametrize(
    ("old", "new", "better", "kind"),
    [
        (Rate(1, 16, 0.01, 0.28), Rate(14, 16, 0.64, 0.97), "higher", "better"),
        (Rate(1, 16, 0.01, 0.28), Rate(14, 16, 0.64, 0.97), "lower", "worse"),
        (Rate(5, 16, 0.14, 0.56), Rate(7, 16, 0.23, 0.67), "higher", "noise"),
        (Rate(14, 16, 0.64, 0.97), Rate(1, 16, 0.01, 0.28), "lower", "better"),
    ],
)
def test_changes_count_only_outside_the_intervals(
    old: object, new: object, better: str, kind: str
) -> None:
    previous = measurement_journal.Point(0, {"series": "s", "metric": "m"}, old)
    assert measurement_journal.compare(new, previous, better).kind == kind
    assert measurement_journal.compare(new, None, better).kind == "first"


def test_the_site_has_both_pages(tmp_path: Path) -> None:
    sys.argv = ["build_site.py", str(tmp_path)]
    assert build_site.main() == 0
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="measurements.html"' in index
    assert index.startswith("<!doctype html>")
    assert "<title>taintwall measurements</title>" in (tmp_path / "measurements.html").read_text(
        encoding="utf-8"
    )
