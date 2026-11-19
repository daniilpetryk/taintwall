# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Print a release's notes from ``CHANGELOG.md``, for the GitHub Release.

The changelog is the only place release notes are written: the release
workflow publishes its section for the tag, so the two cannot differ.

Usage::

    python3 scripts/release_notes.py 0.3.0           # the notes
    python3 scripts/release_notes.py 0.3.0 --title   # "v0.3.0: <summary line>"
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
HEADING = re.compile(
    r"^## \[(?P<version>[^\]]+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?$", re.MULTILINE
)


class MissingVersionError(LookupError):
    """The changelog has no section for the version."""

    def __init__(self, version: str) -> None:
        super().__init__(f"CHANGELOG.md has no section for {version}")


def sections(text: str) -> dict[str, str]:
    """Each version's section body, without its heading and link references."""
    text = re.split(r"^\[[^\]]+\]: ", text, maxsplit=1, flags=re.MULTILINE)[0]
    matches = list(HEADING.finditer(text))
    bodies = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        bodies[match["version"]] = text[match.end() : end].strip()
    return bodies


def notes(version: str, text: str) -> str:
    """The section of ``version``."""
    body = sections(text).get(version)
    if body is None:
        raise MissingVersionError(version)
    return body + "\n"


def title(version: str, text: str) -> str:
    """``v<version>: <the section's first line>``."""
    summary = notes(version, text).splitlines()[0].rstrip(".")
    return f"v{version}: {summary}"


def main() -> int:
    """Print the notes, or with ``--title`` the release title."""
    version, *flags = sys.argv[1:]
    text = CHANGELOG.read_text(encoding="utf-8")
    print(title(version, text) if "--title" in flags else notes(version, text), end="")  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":
    sys.exit(main())
