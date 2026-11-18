# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""On-disk cache of model responses, keyed by request hash.

Writes are atomic (a reader sees nothing or the whole entry), and an
unreadable entry is a miss, so an interrupted run never breaks a rerun.
"""

import contextlib
import os
from pathlib import Path

import pydantic
from openai.types.chat import ChatCompletion


def read_cached(path: Path) -> ChatCompletion | None:
    """A cached response, or ``None`` if there is none or it is unreadable."""
    try:
        return ChatCompletion.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (pydantic.ValidationError, UnicodeDecodeError):
        # A partial write from an older version; refetch and overwrite it.
        return None


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` so that a reader sees either nothing or all of it."""
    partial = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        partial.write_text(text, encoding="utf-8")
        partial.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            partial.unlink()
