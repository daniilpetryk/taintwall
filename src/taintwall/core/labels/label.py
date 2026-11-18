# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Labels: the sources of a value and who may read it.

A join never makes a label cleaner than its inputs: sources only accumulate
and readers only become more restrictive. Crowded collapsible kinds become a
category, which keeps joins associative and idempotent.
"""

import dataclasses
import enum
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Final, Self, override

from taintwall.core.labels.errors import (
    EmptyLabelError,
    FormatProblem,
    InvalidKindError,
    InvalidReadersError,
    LabelFormatError,
    NotASourceError,
)
from taintwall.core.labels.source import COLLAPSIBLE_KINDS, Source, SourceKind

MAX_SOURCES_PER_KIND: Final = 8
"""Above this many distinct sources of a collapsible kind, they become a category."""


class Readers(enum.Enum):
    """Who may see or receive a value, from least to most restrictive."""

    PUBLIC = 0
    """Anyone: on-chain data, public web pages, plan literals."""

    USER = 1
    """Only the owner: address book, private files, chat history."""

    def meet(self, other: Self) -> Self:
        """The more restrictive of the two."""
        return type(self)(max(self.value, other.value))


def _normalize(sources: frozenset[Source]) -> frozenset[Source]:
    """Canonical source set: categories absorb their kind, crowded kinds collapse.

    Applied after every union. Because it only ever replaces sources of one
    kind by that kind's category, it keeps joins associative and idempotent.
    Only ``COLLAPSIBLE_KINDS`` collapse for crowding; a category of another
    kind (loaded from storage) still absorbs its kind.
    """
    per_kind = Counter(source.kind for source in sources)
    collapsed = {
        source.kind
        for source in sources
        if source.is_category
        or (source.kind in COLLAPSIBLE_KINDS and per_kind[source.kind] > MAX_SOURCES_PER_KIND)
    }
    return frozenset(
        Source.category(source.kind) if source.kind in collapsed else source for source in sources
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Label:
    """Provenance of a value: its sources and who may read it.

    Build labels with ``Label.of``. The constructor accepts any iterable of
    sources, always stores a normalized ``frozenset``, and validates every
    field, so equal provenance always compares equal and a label never shares
    mutable state with its caller.
    """

    sources: frozenset[Source]
    readers: Readers = Readers.PUBLIC

    def __post_init__(self) -> None:
        try:
            sources = frozenset(self.sources)
        except TypeError as err:  # not iterable, or unhashable items
            raise NotASourceError(self.sources) from err
        if not sources:
            raise EmptyLabelError
        for item in sources:
            if not isinstance(item, Source):
                raise NotASourceError(item)
        if not isinstance(self.readers, Readers):
            raise InvalidReadersError(self.readers)
        object.__setattr__(self, "sources", _normalize(sources))

    @classmethod
    def of(cls, *sources: Source, readers: Readers = Readers.PUBLIC) -> Self:
        """A label with the given sources and readers."""
        return cls(frozenset(sources), readers)

    def join(self, other: Self) -> Self:
        """Label of a value derived from both: union of sources, stricter readers."""
        return type(self)(self.sources | other.sources, self.readers.meet(other.readers))

    def covers(self, other: Self) -> bool:
        """Whether this label is at least as tainted and as private as ``other``.

        True when every source of ``other`` is covered by a source here and
        the readers here are at least as restrictive.
        """
        sources_covered = all(
            any(mine.covers(theirs) for mine in self.sources) for theirs in other.sources
        )
        return sources_covered and self.readers.value >= other.readers.value

    def all_sources(self, trusted: Callable[[Source], bool]) -> bool:
        """Whether ``trusted`` accepts every source: the general trust predicate.

        A category stands for unknown members of its kind, so it never passes,
        whatever ``trusted`` says about it.
        """
        return all(not source.is_category and trusted(source) for source in self.sources)

    def only_from(self, kinds: Iterable[SourceKind]) -> bool:
        """Whether every source is a precise source of one of ``kinds``."""
        allowed = frozenset(kinds)
        for kind in allowed:
            if not isinstance(kind, SourceKind):
                raise InvalidKindError(kind)
        return self.all_sources(lambda source: source.kind in allowed)

    @property
    def kinds(self) -> frozenset[SourceKind]:
        """Every kind of source this value depends on."""
        return frozenset(source.kind for source in self.sources)

    @property
    def principals(self) -> frozenset[str]:
        """Identities of non-owner principals this value depends on.

        A collapsed principal category shows up as ``*``.
        """
        return frozenset(
            "*" if s.name is None else s.name
            for s in self.sources
            if s.kind is SourceKind.PRINCIPAL
        )

    def to_json(self) -> dict[str, object]:
        """Plain-data form for storage, with sources in a stable order."""
        ordered = sorted(self.sources, key=str)
        return {
            "v": LABEL_FORMAT_VERSION,
            "sources": [source.to_json() for source in ordered],
            "readers": _READERS_CODE[self.readers],
        }

    @classmethod
    def from_json(cls, data: object, *, _mint: object = None) -> Self:
        """Rebuild a label from ``to_json`` output, validating every field.

        Labels with trusted sources load only through ``taintwall.core.mint``.

        Raises:
            LabelFormatError: for any malformed input, never another error.
        """
        if not isinstance(data, Mapping) or set(data) != {"v", "sources", "readers"}:
            raise LabelFormatError(FormatProblem.LABEL_SHAPE)
        version, raw_sources, raw_readers = data["v"], data["sources"], data["readers"]
        if type(version) is not int or version != LABEL_FORMAT_VERSION:
            raise LabelFormatError(FormatProblem.VERSION)
        if type(raw_sources) is not list:
            raise LabelFormatError(FormatProblem.SOURCES_TYPE)
        if not raw_sources:
            raise LabelFormatError(FormatProblem.NO_SOURCES)
        readers = _READERS_BY_CODE.get(raw_readers) if type(raw_readers) is str else None
        if readers is None:
            raise LabelFormatError(FormatProblem.READERS)
        return cls(frozenset(Source.from_json(item, _mint=_mint) for item in raw_sources), readers)

    @override
    def __str__(self) -> str:
        names = ", ".join(sorted(str(source) for source in self.sources))
        return f"{{{names}}} readers={self.readers.name.lower()}"


LABEL_FORMAT_VERSION: Final = 1
"""Version of the ``to_json`` format; bump it, and add a migration, on any change."""

# Explicit, stable storage codes: renaming an enum member must not break stored labels.
_READERS_CODE: Final = {Readers.PUBLIC: "public", Readers.USER: "user"}
_READERS_BY_CODE: Final = {code: readers for readers, code in _READERS_CODE.items()}


def join_all(first: Label, *rest: Label) -> Label:
    """Join any number of labels."""
    result = first
    for label in rest:
        result = result.join(label)
    return result
