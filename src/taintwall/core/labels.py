# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Provenance labels: where a value came from and who may see it.

Every value the interpreter handles is a ``Labeled[T]``: the value plus a
``Label``. A label records two things:

* ``sources``: every origin the value may depend on (a tool response, a plan
  literal, the address book, a principal in a group chat). Trust is not stored
  on the label; policies compute it from the sources (design decision Q1).
* ``readers``: who may see or receive the value (decision Q9).

Combining values joins their labels. A join never makes a label cleaner than
its inputs: sources only accumulate and readers only become more restrictive.
"""

import enum
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Self, override

CATEGORY: Final = "*"
"""Name of a collapsed source that stands for every source of its kind."""

MAX_SOURCES_PER_KIND: Final = 8
"""Above this many distinct sources of one kind, they collapse into a category."""


class SourceKind(enum.StrEnum):
    """Where a value can come from."""

    PLAN = "plan"
    """A literal written by the planner, which never sees untrusted data."""

    TOOL = "tool"
    """A tool response. Whether it is trusted depends on the tool's policy."""

    ADDRESS_BOOK = "address_book"
    """An entry the owner added to the address book."""

    USER_CONFIRMED = "user_confirmed"
    """A value the owner confirmed for one specific call."""

    VERIFIED_LIST = "verified_list"
    """An entry from a curated list, such as verified token contracts."""

    PRINCIPAL = "principal"
    """Who made the request or wrote the data: the owner or another person."""

    SYSTEM = "system"
    """Produced by the runtime itself, such as gas estimates or timestamps."""


OWNER: Final = "owner"
"""Principal name of the wallet owner; every other principal is a handle."""


class InvalidSourceError(ValueError):
    """A source name is empty or contains whitespace."""

    def __init__(self, kind: SourceKind, name: str) -> None:
        super().__init__(f"invalid {kind.value} source name: {name!r}")


class EmptyLabelError(ValueError):
    """A label was created without any source."""

    def __init__(self) -> None:
        super().__init__("a label needs at least one source")


@dataclass(frozen=True, slots=True, order=True)
class Source:
    """One origin of a value, such as ``tool:read_file`` or ``principal:owner``.

    The name ``*`` marks a category: it stands for every source of that kind
    and is what many sources of one kind collapse into.
    """

    kind: SourceKind
    name: str

    def __post_init__(self) -> None:
        if not self.name or any(ch.isspace() for ch in self.name):
            raise InvalidSourceError(self.kind, self.name)

    @classmethod
    def plan(cls) -> Self:
        """A literal from the planner."""
        return cls(SourceKind.PLAN, "literal")

    @classmethod
    def tool(cls, name: str) -> Self:
        """The response of the tool called ``name``."""
        return cls(SourceKind.TOOL, name)

    @classmethod
    def principal(cls, name: str) -> Self:
        """A request or data from ``name``; use ``OWNER`` for the wallet owner."""
        return cls(SourceKind.PRINCIPAL, name)

    @classmethod
    def category(cls, kind: SourceKind) -> Self:
        """Every source of ``kind`` at once."""
        return cls(kind, CATEGORY)

    @property
    def is_category(self) -> bool:
        """Whether this source stands for its whole kind."""
        return self.name == CATEGORY

    def covers(self, other: Self) -> bool:
        """Whether this source is ``other`` or a category that includes it."""
        return self == other or (self.is_category and self.kind == other.kind)

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}:{self.name}"


class Readers(enum.Enum):
    """Who may see or receive a value, from least to most restrictive."""

    PUBLIC = 0
    """Anyone: on-chain data, public web pages, plan literals."""

    USER = 1
    """Only the owner: address book, private files, chat history."""

    def meet(self, other: Self) -> Self:
        """The more restrictive of the two."""
        return self if self.value >= other.value else other


def _normalize(sources: Iterable[Source]) -> frozenset[Source]:
    """Canonical source set: categories absorb their kind, crowded kinds collapse.

    Applied after every union. Because it only ever replaces sources of one
    kind by that kind's category, it keeps joins associative and idempotent.
    """
    pool = frozenset(sources)
    per_kind = Counter(source.kind for source in pool)
    collapsed = {
        source.kind
        for source in pool
        if source.is_category or per_kind[source.kind] > MAX_SOURCES_PER_KIND
    }
    return frozenset(
        Source.category(source.kind) if source.kind in collapsed else source for source in pool
    )


@dataclass(frozen=True, slots=True)
class Label:
    """Provenance of a value: its sources and who may read it.

    Build labels with ``Label.of``; the constructor normalizes the source set,
    so equal provenance always compares equal.
    """

    sources: frozenset[Source]
    readers: Readers = Readers.PUBLIC

    def __post_init__(self) -> None:
        if not self.sources:
            raise EmptyLabelError
        normalized = _normalize(self.sources)
        if normalized != self.sources:
            object.__setattr__(self, "sources", normalized)

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

    @property
    def kinds(self) -> frozenset[SourceKind]:
        """Every kind of source this value depends on."""
        return frozenset(source.kind for source in self.sources)

    @property
    def principals(self) -> frozenset[str]:
        """Names of the principals this value depends on."""
        return frozenset(s.name for s in self.sources if s.kind is SourceKind.PRINCIPAL)

    def only_from(self, kinds: Iterable[SourceKind]) -> bool:
        """Whether every source is of one of ``kinds``: the basic trust predicate."""
        return self.kinds <= frozenset(kinds)

    @override
    def __str__(self) -> str:
        names = ", ".join(sorted(str(source) for source in self.sources))
        return f"{{{names}}} readers={self.readers.name.lower()}"


def join_all(first: Label, *rest: Label) -> Label:
    """Join any number of labels."""
    result = first
    for label in rest:
        result = result.join(label)
    return result


@dataclass(frozen=True, slots=True)
class Labeled[T]:
    """A value together with its provenance label.

    There is no operator overloading: arithmetic or string operations on
    labeled values go through the interpreter, which joins labels explicitly.
    """

    value: T
    label: Label

    def derive[U](self, value: U, *inputs: "Labeled[object]") -> "Labeled[U]":
        """A new value computed from this one and ``inputs``, labeled with all of them."""
        return Labeled(value, join_all(self.label, *(item.label for item in inputs)))
