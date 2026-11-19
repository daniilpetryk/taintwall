# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The changelog: well-formed, in order, and the source of release notes."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_notes", ROOT / "scripts" / "release_notes.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_notes"] = module
    spec.loader.exec_module(module)
    return module


release_notes = _load()
HEADINGS = [match.groupdict() for match in release_notes.HEADING.finditer(TEXT)]
RELEASES = [heading for heading in HEADINGS if heading["version"] != "Unreleased"]


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def test_unreleased_comes_first_without_a_date() -> None:
    assert HEADINGS[0] == {"version": "Unreleased", "date": None}


def test_releases_are_semver_newest_first_with_dates() -> None:
    versions = [heading["version"] for heading in RELEASES]
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", version) for version in versions)
    assert versions == sorted(versions, key=_key, reverse=True)
    dates = [heading["date"] for heading in RELEASES]
    assert None not in dates
    assert dates == sorted(dates, reverse=True)


def test_every_heading_has_a_link() -> None:
    for heading in HEADINGS:
        assert f"\n[{heading['version']}]: https://github.com/" in TEXT, heading["version"]


@pytest.mark.parametrize("version", [heading["version"] for heading in RELEASES])
def test_notes_are_the_section_and_open_with_a_summary(version: str) -> None:
    notes = release_notes.notes(version, TEXT)
    assert "## [" not in notes
    assert "]: https://" not in notes
    title = release_notes.title(version, TEXT)
    assert title.startswith(f"v{version}: ")
    assert not title.endswith(".")


def test_a_missing_version_is_an_error() -> None:
    with pytest.raises(release_notes.MissingVersionError, match=r"9\.9\.9"):
        release_notes.notes("9.9.9", TEXT)
