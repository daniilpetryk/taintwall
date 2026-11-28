# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The decision log: numbered without gaps, complete, and indexed."""

import re
from pathlib import Path

import pytest

DECISIONS = Path(__file__).resolve().parent.parent / "docs" / "decisions"
RECORDS = sorted(DECISIONS.glob("[0-9][0-9][0-9][0-9]-*.md"))
INDEX = (DECISIONS / "README.md").read_text(encoding="utf-8")
STATUSES = {"proposed", "accepted", "rejected", "superseded", "deprecated"}


def test_numbers_have_no_gaps() -> None:
    numbers = [int(path.name[:4]) for path in RECORDS]
    assert numbers == list(range(1, len(numbers) + 1))


@pytest.mark.parametrize("record", RECORDS, ids=lambda path: path.name[:4])
def test_each_record_has_its_parts_and_is_indexed(record: Path) -> None:
    text = record.read_text(encoding="utf-8")
    assert text.startswith(f"# {int(record.name[:4])}. ")
    status = re.search(r"^- \*\*Status:\*\* (\w+)", text, re.MULTILINE)
    assert status is not None
    assert status[1] in STATUSES
    assert re.search(r"^- \*\*Date:\*\* \d{4}-\d{2}-\d{2}$", text, re.MULTILINE)
    for section in ("## Context", "## Decision", "## Consequences"):
        assert section in text
    assert f"]({record.name})" in INDEX


def test_dates_do_not_go_backwards() -> None:
    dates = [
        re.search(r"\*\*Date:\*\* (\S+)", path.read_text(encoding="utf-8"))[1]  # type: ignore[index]
        for path in RECORDS[1:]  # 0001 records the practice itself, written after 0002-0014
    ]
    assert dates == sorted(dates)
