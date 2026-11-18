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

Everything here is validated strictly, because the inputs are partly
attacker-controlled (chat handles, tool names from MCP servers, tool output):

* kinds must be ``SourceKind`` members and readers ``Readers`` members; plain
  strings are rejected, since ``"owner"`` would otherwise compare equal to
  ``SourceKind.OWNER``;
* names must match a strict ASCII pattern; no string becomes the owner or a
  category, which are expressed by the kind and by ``name=None``;
* labeled values must be immutable, checked by exact type and with a depth
  limit, so a value cannot change behind its label.
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
"""Above this many distinct sources of a collapsible kind, they become a category."""

MAX_NAME_LENGTH: Final = 128
"""Longest accepted source name."""

MAX_VALUE_DEPTH: Final = 64
"""Deepest nesting of tuples, frozensets and records accepted in a labeled value."""

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


_COLLAPSIBLE_KINDS: Final = frozenset({SourceKind.TOOL, SourceKind.PRINCIPAL, SourceKind.SYSTEM})
"""Kinds whose distinct sources can grow without bound inside loops.

The others are bounded by what the owner curated (address book, verified list,
confirmations) or are singletons (plan, owner), so they always stay precise and
keep their trust however many of them a value depends on.
"""


def _preview(value: object) -> str:
    return repr(value)[:_ERROR_PREVIEW]


class InvalidSourceError(ValueError):
    """A source name is malformed, or does not fit its kind."""

    def __init__(self, kind: object, name: object) -> None:
        super().__init__(f"invalid source name for {_preview(kind)}: {_preview(name)}")


class InvalidKindError(TypeError):
    """A source kind is not a ``SourceKind`` member."""

    def __init__(self, kind: object) -> None:
        super().__init__(f"source kind must be a SourceKind, got {_preview(kind)}")


class InvalidReadersError(TypeError):
    """Label readers are not a ``Readers`` member."""

    def __init__(self, readers: object) -> None:
        super().__init__(f"readers must be a Readers member, got {_preview(readers)}")


class NotASourceError(TypeError):
    """A label was given something that is not a ``Source``."""

    def __init__(self, item: object) -> None:
        super().__init__(f"label sources must be Source objects, got {_preview(item)}")


class EmptyLabelError(ValueError):
    """A label was created without any source."""

    def __init__(self) -> None:
        super().__init__("a label needs at least one source")


class MutableValueError(TypeError):
    """A labeled value could change after labeling without its label changing."""

    def __init__(self, value: object) -> None:
        super().__init__(f"labeled values must be immutable, got {type(value).__name__}")


class ValueTooDeepError(ValueError):
    """A labeled value is nested deeper than ``MAX_VALUE_DEPTH``."""

    def __init__(self) -> None:
        super().__init__(f"labeled value is nested deeper than {MAX_VALUE_DEPTH} levels")


class FormatProblem(enum.Enum):
    """What is wrong with serialized label data."""

    SOURCE_SHAPE = "source must be an object with exactly kind and name"
    SOURCE_KIND = "unknown source kind"
    SOURCE_NAME_TYPE = "source name must be a string or null"
    SOURCE_NAME = "source name is not valid for its kind"
    LABEL_SHAPE = "label must be an object with exactly sources and readers"
    SOURCES_TYPE = "sources must be a list"
    NO_SOURCES = "a label needs at least one source"
    READERS = "unknown readers"


class LabelFormatError(ValueError):
    """Serialized label data does not have the expected shape.

    The only error ``Source.from_json`` and ``Label.from_json`` raise.
    """

    def __init__(self, problem: FormatProblem) -> None:
        self.problem = problem
        super().__init__(f"malformed label data: {problem.value}")


@dataclasses.dataclass(frozen=True, slots=True)
class Source:
    """One origin of a value, such as ``tool:read_file`` or ``owner``.

    ``name=None`` marks a category: it stands for every source of that kind
    and is what many sources of one kind collapse into. A category can only be
    created in code (``Source.category``) or loaded from storage, never parsed
    from a name.
    """

    kind: SourceKind
    name: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceKind):
            raise InvalidKindError(self.kind)
        if self.kind is SourceKind.OWNER:
            if self.name != _OWNER_NAME:
                raise InvalidSourceError(self.kind, self.name)
            return
        name: object = self.name  # checked at runtime: callers may break the annotation
        if name is None:
            return
        if (
            not isinstance(name, str)
            or len(name) > MAX_NAME_LENGTH
            or not _NAME_PATTERN.fullmatch(name)
        ):
            raise InvalidSourceError(self.kind, name)

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
        return self == other or (self.is_category and self.kind is other.kind)

    def to_json(self) -> dict[str, str | None]:
        """Plain-data form for storage."""
        return {"kind": self.kind.value, "name": self.name}

    @classmethod
    def from_json(cls, data: object) -> Self:
        """Rebuild a source from ``to_json`` output, validating every field.

        Raises:
            LabelFormatError: for any malformed input.
        """
        if not isinstance(data, Mapping) or set(data) != {"kind", "name"}:
            raise LabelFormatError(FormatProblem.SOURCE_SHAPE)
        kind, name = data["kind"], data["name"]
        if type(kind) is not str or kind not in SourceKind:
            raise LabelFormatError(FormatProblem.SOURCE_KIND)
        if name is not None and type(name) is not str:
            raise LabelFormatError(FormatProblem.SOURCE_NAME_TYPE)
        try:
            return cls(SourceKind(kind), name)
        except InvalidSourceError as err:
            raise LabelFormatError(FormatProblem.SOURCE_NAME) from err

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


def _normalize(sources: frozenset[Source]) -> frozenset[Source]:
    """Canonical source set: categories absorb their kind, crowded kinds collapse.

    Applied after every union. Because it only ever replaces sources of one
    kind by that kind's category, it keeps joins associative and idempotent.
    Only ``_COLLAPSIBLE_KINDS`` collapse for crowding; a category of another
    kind (loaded from storage) still absorbs its kind.
    """
    per_kind = Counter(source.kind for source in sources)
    collapsed = {
        source.kind
        for source in sources
        if source.is_category
        or (source.kind in _COLLAPSIBLE_KINDS and per_kind[source.kind] > MAX_SOURCES_PER_KIND)
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
        sources = frozenset(self.sources)
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
        """Rebuild a label from ``to_json`` output, validating every field.

        Raises:
            LabelFormatError: for any malformed input, never another error.
        """
        if not isinstance(data, Mapping) or set(data) != {"sources", "readers"}:
            raise LabelFormatError(FormatProblem.LABEL_SHAPE)
        raw_sources, raw_readers = data["sources"], data["readers"]
        if type(raw_sources) is not list:
            raise LabelFormatError(FormatProblem.SOURCES_TYPE)
        if not raw_sources:
            raise LabelFormatError(FormatProblem.NO_SOURCES)
        readers = _READERS_BY_NAME.get(raw_readers) if type(raw_readers) is str else None
        if readers is None:
            raise LabelFormatError(FormatProblem.READERS)
        return cls(frozenset(Source.from_json(item) for item in raw_sources), readers)

    @override
    def __str__(self) -> str:
        names = ", ".join(sorted(str(source) for source in self.sources))
        return f"{{{names}}} readers={self.readers.name.lower()}"


_READERS_BY_NAME: Final = {member.name.lower(): member for member in Readers}


def join_all(first: Label, *rest: Label) -> Label:
    """Join any number of labels."""
    result = first
    for label in rest:
        result = result.join(label)
    return result


_IMMUTABLE_ATOMS: Final = frozenset(
    {
        type(None),
        bool,
        int,
        float,
        complex,
        str,
        bytes,
        Decimal,
        fractions.Fraction,
        dt.date,
        dt.timedelta,
    }
)
"""Matched by exact type: a subclass could add mutable instance attributes."""

_TIMEZONE_AWARE: Final = frozenset({dt.datetime, dt.time})


def _check_immutable(value: object) -> None:
    """Raise unless ``value`` cannot change after it has been labeled.

    Accepts, by exact type, immutable atoms; tuples and frozensets of
    immutable values; naive or fixed-offset datetimes and times; enum members
    whose value is immutable; frozen dataclasses with immutable fields; and
    nested ``Labeled`` values (already checked when they were built, so they
    are not re-scanned). Iterative, with a depth limit, so attacker-controlled
    nesting cannot exhaust the stack.

    Raises:
        MutableValueError: if any part of ``value`` could change.
        ValueTooDeepError: if nesting exceeds ``MAX_VALUE_DEPTH``.
    """
    pending: list[tuple[object, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_VALUE_DEPTH:
            raise ValueTooDeepError
        item_type = type(item)
        if item_type in _IMMUTABLE_ATOMS or isinstance(item, Labeled):
            continue
        if item_type in _TIMEZONE_AWARE:
            tzinfo = getattr(item, "tzinfo", None)
            if tzinfo is not None and type(tzinfo) is not dt.timezone:
                raise MutableValueError(tzinfo)
            continue
        if isinstance(item, enum.Enum):
            pending.append((item.value, depth + 1))
            continue
        if item_type is tuple or item_type is frozenset:
            pending.extend((child, depth + 1) for child in item)  # type: ignore[attr-defined]
            continue
        if _is_frozen_dataclass(item):
            pending.extend(
                (getattr(item, field.name), depth + 1)
                for field in dataclasses.fields(item)  # type: ignore[arg-type]
            )
            continue
        raise MutableValueError(item)


def _is_frozen_dataclass(item: object) -> bool:
    if not dataclasses.is_dataclass(item) or isinstance(item, type):
        return False
    params = getattr(type(item), "__dataclass_params__", None)
    return bool(getattr(params, "frozen", False))


@dataclasses.dataclass(frozen=True, slots=True)
class Labeled[T]:
    """A value together with its provenance label.

    The value must be immutable (see ``_check_immutable``), so it cannot change
    behind its label. There is no operator overloading: operations on labeled
    values go through the interpreter, which joins labels explicitly.
    """

    value: T
    label: Label

    def __post_init__(self) -> None:
        if not isinstance(self.label, Label):
            raise NotALabelError(self.label)
        _check_immutable(self.value)

    def derive[U](self, value: U, *inputs: "Labeled[object]") -> "Labeled[U]":
        """A new value computed from this one and ``inputs``, labeled with all of them."""
        return Labeled(value, join_all(self.label, *(item.label for item in inputs)))


class NotALabelError(TypeError):
    """A labeled value was given something that is not a ``Label``."""

    def __init__(self, label: object) -> None:
        super().__init__(f"label must be a Label, got {_preview(label)}")
