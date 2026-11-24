# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""An on-disk cache of structured replies, keyed by the request's hash.

The budget rule: a rerun costs nothing. A reply is stored only once it has
passed the inner client's checks; writes are atomic, and an unreadable entry
is a miss, so an interrupted run never breaks the next one. A cached reply
reports no usage (it cost nothing) and ``cached=True``.
"""

import contextlib
import dataclasses
import hashlib
import json
import os
from pathlib import Path

from taintwall.llm.client import LLMClient, StructuredReply, StructuredRequest


def key(request: StructuredRequest) -> str:
    """The request's hash: every field, canonically ordered."""
    canonical = json.dumps(dataclasses.asdict(request), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class CachedClient:
    """An ``LLMClient`` that answers a request it has seen from disk."""

    def __init__(self, inner: LLMClient, directory: Path) -> None:
        self._inner = inner
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def structured(self, request: StructuredRequest) -> StructuredReply:
        """The cached reply, or the inner client's, then cached."""
        path = self._directory / f"{key(request)}.json"
        value = _read(path)
        if value is not None:
            return StructuredReply(value, cached=True)
        reply = self._inner.structured(request)
        _write_atomic(path, json.dumps(reply.value, sort_keys=True))
        return reply


def _read(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` so that a reader sees either nothing or all of it."""
    partial = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        partial.write_text(text, encoding="utf-8")
        partial.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            partial.unlink()
