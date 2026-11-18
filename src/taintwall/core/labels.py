# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Provenance labels: where a value came from and who may see it.

Every value the interpreter handles is a ``Labeled[T]``: the value plus a
``Label``. A label records two things:

* ``sources``: every origin the value may depend on (a tool response, a plan
  literal, the address book, the owner or another person in a chat). Trust is
  not stored on the label; policies compute it from the sources (decision Q1).
* ``readers``: who may see or receive the value (decision Q9).

Combining values joins their labels. A join never makes a label cleaner than
its inputs: sources only accumulate and readers only become more restrictive.

Names inside sources often come from outside (a chat handle, a tool name from
an MCP server), so they are validated against a strict ASCII pattern. No
external string can become the owner or a category: both are expressed by the
source kind and by ``name=None``, never by a name.
"""

import dataclasses
import datetime as dt
import enum
import fractions
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Final, Self, override

MAX_SOURCES_PER_KIND: Final = 8
"""Above this many distinct sources of one kind, they collapse into a category."""

MAX_NAME_LENGTH: Final = 128
"""Longest accepted source name."""

_NAME_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]*")
_OWNER_NAME: Final = "owner"
_ERROR_PREVIEW: Final = 64


class SourceKind(enum.StrEnum):
    """Where a value can come from."""

    PLAN = "plan"
    """A literal written by the planner, which never sees untrusted data."""

    OWNER = "owner"
    """The wallet owner, identified by config, never by message content."""

    PRINCIPAL = "principal"
    """Anyone else who sends requests or data, such as a group-chat member."""

    TOOL = "tool"
    """A tool response. Whether it is trusted depends on the tool's policy."""

    ADDRESS_BOOK = "address_book"
    """An entry the owner added to the address book."""

    USER_CONFIRMED = "user_confirmed"
    """A value the owner confirmed for one specific call, named by the call ID."""

    VERIFIED_LIST = "verified_list"
    """An entry from a curated list, such as verified token contracts."""

    SYSTEM = "system"
    """Produced by the runtime itself, such as gas estimates or timestamps."""


class InvalidSourceError(ValueError):
    """A source name is malformed, or does not fit its kind."""

    def __init__(self, kind: SourceKind, name: object) -> None:
        preview = repr(name)[:_ERROR_PREVIEW]
        super().__init__(f"invalid {kind.value} source name: {preview}")


class EmptyLabelError(ValueError):
    """A label was created without any source."""

    def __init__(self) -> None:
        super().__init__("a label needs at least one source")


class MutableValueError(TypeError):
    """A labeled value could change after labeling without its label changing."""

    def __init__(self, value: object) -> None:
        super().__init__(f"labeled values must be immutable, got {type(value).__name__}")


class FormatProblem(enum.Enum):
    """What is wrong with serialized label data."""

    SOURCE_SHAPE = "source must be an object with exactly kind and name"
    SOURCE_KIND = "unknown source kind"
    SOURCE_NAME_TYPE = "source name must be a string or null"
    LABEL_SHAPE = "label must be an object with exactly sources and readers"
    SOURCES_TYPE = "sources must be a list"
    READERS = "unknown readers"


class LabelFormatError(ValueError):
    """Serialized label data does not have the expected shape."""

    def __init__(self, problem: FormatProblem) -> None:
        self.problem = problem
        super().__init__(f"malformed label data: {problem.value}")


@dataclasses.dataclass(frozen=True, slots=True)
class Source:
    """One origin of a value, such as ``tool:read_file`` or ``owner``.

    ``name=None`` marks a category: it stands for every source of that kind
    and is what many sources of one kind collapse into. A category can only be
    created in code (``Source.category``), never parsed from a name.
    """

    kind: SourceKind
    name: str | None

    def __post_init__(self) -> None:
        if self.kind is SourceKind.OWNER:
            if self.name != _OWNER_NAME:
                raise InvalidSourceError(self.kind, self.name)
            return
        if self.name is None:
            return
        if len(self.name) > MAX_NAME_LENGTH or not _NAME_PATTERN.fullmatch(self.name):
            raise InvalidSourceError(self.kind, self.name)

    @classmethod
    def plan(cls) -> Self:
        """A literal from the planner."""
        return cls(SourceKind.PLAN, "literal")

    @classmethod
    def owner(cls) -> Self:
        """The wallet owner."""
        return cls(SourceKind.OWNER, _OWNER_NAME)

    @classmethod
    def principal(cls, identity: str) -> Self:
        """Someone other than the owner, by platform identity (``discord:123``)."""
        return cls(SourceKind.PRINCIPAL, identity)

    @classmethod
    def tool(cls, name: str) -> Self:
        """The response of the tool called ``name``."""
        return cls(SourceKind.TOOL, name)

    @classmethod
    def user_confirmed(cls, call_id: str) -> Self:
        """A value the owner confirmed for the call ``call_id`` only."""
        return cls(SourceKind.USER_CONFIRMED, call_id)

    @classmethod
    def category(cls, kind: SourceKind) -> Self:
        """Every source of ``kind`` at once. The owner has no category."""
        if kind is SourceKind.OWNER:
            raise InvalidSourceError(kind, None)
        return cls(kind, None)

    @property
    def is_category(self) -> bool:
        """Whether this source stands for its whole kind."""
        return self.name is None

    def covers(self, other: Self) -> bool:
        """Whether this source is ``other`` or a category that includes it."""
        return self == other or (self.is_category and self.kind == other.kind)

    def to_json(self) -> dict[str, str | None]:
        """Plain-data form for storage."""
        return {"kind": self.kind.value, "name": self.name}

    @classmethod
    def from_json(cls, data: object) -> Self:
        """Rebuild a source from ``to_json`` output, validating every field."""
        if not isinstance(data, Mapping) or set(data) != {"kind", "name"}:
            raise LabelFormatError(FormatProblem.SOURCE_SHAPE)
        kind, name = data["kind"], data["name"]
        if not isinstance(kind, str) or kind not in SourceKind:
            raise LabelFormatError(FormatProblem.SOURCE_KIND)
        if name is not None and not isinstance(name, str):
            raise LabelFormatError(FormatProblem.SOURCE_NAME_TYPE)
        return cls(SourceKind(kind), name)

    @override
    def __str__(self) -> str:
        if self.kind is SourceKind.OWNER:
            return self.kind.value
        return f"{self.kind.value}:{'*' if self.name is None else self.name}"


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


@dataclasses.dataclass(frozen=True, slots=True)
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

    def all_sources(self, trusted: Callable[[Source], bool]) -> bool:
        """Whether ``trusted`` accepts every source: the general trust predicate.

        A category stands for unknown members of its kind, so it never passes,
        whatever ``trusted`` says about it.
        """
        return all(not source.is_category and trusted(source) for source in self.sources)

    def only_from(self, kinds: Iterable[SourceKind]) -> bool:
        """Whether every source is a precise source of one of ``kinds``."""
        allowed = frozenset(kinds)
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
            "sources": [source.to_json() for source in ordered],
            "readers": self.readers.name.lower(),
        }

    @classmethod
    def from_json(cls, data: object) -> Self:
        """Rebuild a label from ``to_json`` output, validating every field."""
        if not isinstance(data, Mapping) or set(data) != {"sources", "readers"}:
            raise LabelFormatError(FormatProblem.LABEL_SHAPE)
        raw_sources, raw_readers = data["sources"], data["readers"]
        if not isinstance(raw_sources, list):
            raise LabelFormatError(FormatProblem.SOURCES_TYPE)
        readers = {member.name.lower(): member for member in Readers}.get(str(raw_readers))
        if readers is None:
            raise LabelFormatError(FormatProblem.READERS)
        return cls(frozenset(Source.from_json(item) for item in raw_sources), readers)

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


_IMMUTABLE_ATOMS: Final = (
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
    Decimal,
    fractions.Fraction,
    enum.Enum,
    dt.date,
    dt.time,
    dt.timedelta,
)


def _is_immutable(value: object) -> bool:
    """Whether ``value`` cannot change after it has been labeled.

    Accepts immutable atoms, tuples and frozensets of immutable values, and
    frozen dataclasses (which includes nested ``Labeled`` values for per-field
    labels) whose fields are immutable.
    """
    if isinstance(value, _IMMUTABLE_ATOMS):
        return True
    if isinstance(value, (tuple, frozenset)):
        return all(_is_immutable(item) for item in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        params = getattr(type(value), "__dataclass_params__", None)
        return bool(getattr(params, "frozen", False)) and all(
            _is_immutable(getattr(value, field.name)) for field in dataclasses.fields(value)
        )
    return False


@dataclasses.dataclass(frozen=True, slots=True)
class Labeled[T]:
    """A value together with its provenance label.

    The value must be immutable (see ``_is_immutable``), so it cannot change
    behind its label. There is no operator overloading: operations on labeled
    values go through the interpreter, which joins labels explicitly.
    """

    value: T
    label: Label

    def __post_init__(self) -> None:
        if not _is_immutable(self.value):
            raise MutableValueError(self.value)

    def derive[U](self, value: U, *inputs: "Labeled[object]") -> "Labeled[U]":
        """A new value computed from this one and ``inputs``, labeled with all of them."""
        return Labeled(value, join_all(self.label, *(item.label for item in inputs)))
