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
  limit, so a value cannot change behind its label;
* precise sources of trusted kinds (``TRUSTED_KINDS``) can only be created
  through ``taintwall.core.mint``, which the integration layers (llm, tools,
  web3) are forbidden to import. Untrusted code can still describe where its
  data came from, but it cannot create trusted provenance out of nothing.
"""

import cmath
import dataclasses
import datetime as dt
import enum
import fractions
import math
import re
import reprlib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Final, Self, override
from zoneinfo import ZoneInfo

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

TRUSTED_KINDS: Final = frozenset(
    {
        SourceKind.PLAN,
        SourceKind.OWNER,
        SourceKind.ADDRESS_BOOK,
        SourceKind.VERIFIED_LIST,
        SourceKind.USER_CONFIRMED,
    }
)
"""Kinds a policy may treat as trusted; only ``taintwall.core.mint`` creates them."""


class _MintToken:
    """Capability that ``taintwall.core.mint`` passes to create trusted sources."""

    __slots__ = ()


_MINT_TOKEN: Final = _MintToken()


_REPR: Final = reprlib.Repr(maxstring=_ERROR_PREVIEW, maxother=_ERROR_PREVIEW)


def _preview(value: object) -> str:
    """Bounded repr for error messages.

    ``reprlib`` caps the size and falls back to ``<Type instance at ...>`` when
    an object's own ``__repr__`` raises, so a hostile value cannot replace our
    typed error with its own exception.
    """
    return _REPR.repr(value)[:_ERROR_PREVIEW]


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


class UnmintedSourceError(PermissionError):
    """A trusted source was created outside ``taintwall.core.mint``."""

    def __init__(self, kind: SourceKind) -> None:
        super().__init__(f"{kind.value} sources can only be created by taintwall.core.mint")


class EmptyLabelError(ValueError):
    """A label was created without any source."""

    def __init__(self) -> None:
        super().__init__("a label needs at least one source")


class MutableValueError(TypeError):
    """A labeled value could change after labeling without its label changing."""

    def __init__(self, value: object) -> None:
        super().__init__(f"labeled values must be immutable, got {type(value).__name__}")


class NonFiniteNumberError(ValueError):
    """A labeled number is NaN or infinite.

    Rejected outright: ``NaN > limit`` is False, so a policy check such as
    ``if amount > limit: deny`` would let a NaN amount through.
    """

    def __init__(self, value: object) -> None:
        super().__init__(f"labeled numbers must be finite, got {_preview(value)}")


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
    UNMINTED = "trusted sources can only be loaded through taintwall.core.mint"
    VERSION = "unsupported label format version"
    LABEL_SHAPE = "label must be an object with exactly v, sources and readers"
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
    _mint: object = dataclasses.field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceKind):
            raise InvalidKindError(self.kind)
        name: object = self.name  # checked at runtime: callers may break the annotation
        if self.kind is SourceKind.OWNER:
            if name != _OWNER_NAME:
                raise InvalidSourceError(self.kind, name)
        elif name is not None and (
            not isinstance(name, str)
            or len(name) > MAX_NAME_LENGTH
            or not _NAME_PATTERN.fullmatch(name)
        ):
            raise InvalidSourceError(self.kind, name)
        if self.kind in TRUSTED_KINDS and name is not None and self._mint is not _MINT_TOKEN:
            raise UnmintedSourceError(self.kind)
        # Never keep the capability on the instance, where anyone could read it.
        object.__setattr__(self, "_mint", None)

    @classmethod
    def principal(cls, identity: str) -> Self:
        """Someone other than the owner, by platform identity (``discord:123``)."""
        return cls(SourceKind.PRINCIPAL, identity)

    @classmethod
    def tool(cls, name: str) -> Self:
        """The response of the tool called ``name``."""
        return cls(SourceKind.TOOL, name)

    @classmethod
    def category(cls, kind: SourceKind) -> Self:
        """Every source of ``kind`` at once. The owner has no category.

        Categories need no minting: they never pass a trust predicate.
        """
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
    def from_json(cls, data: object, *, _mint: object = None) -> Self:
        """Rebuild a source from ``to_json`` output, validating every field.

        Trusted sources load only through ``taintwall.core.mint``.

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
            return cls(SourceKind(kind), name, _mint)
        except InvalidSourceError as err:
            raise LabelFormatError(FormatProblem.SOURCE_NAME) from err
        except UnmintedSourceError as err:
            raise LabelFormatError(FormatProblem.UNMINTED) from err

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
_IMMUTABLE_TIMEZONES: Final = frozenset({dt.timezone, ZoneInfo})


class _ReferenceEnum(enum.Enum):
    MEMBER = 0


_ENUM_MEMBER_ATTRS: Final = frozenset(vars(_ReferenceEnum.MEMBER))
"""Attributes every enum member has; anything more is extra, possibly mutable, state."""


def _check_finite(item: object, item_type: type) -> None:
    if item_type is float and not math.isfinite(item):  # type: ignore[arg-type]
        raise NonFiniteNumberError(item)
    if item_type is complex and not cmath.isfinite(item):  # type: ignore[arg-type]
        raise NonFiniteNumberError(item)
    if item_type is Decimal and not item.is_finite():  # type: ignore[attr-defined]
        raise NonFiniteNumberError(item)


def _check_immutable(value: object) -> None:
    """Raise unless ``value`` cannot change after it has been labeled.

    Accepts, by exact type, finite immutable atoms; tuples (and NamedTuples)
    and frozensets of immutable values; datetimes and times that are naive or
    use a fixed offset or ``ZoneInfo``; enum members with an immutable value and
    no extra attributes; frozen dataclasses with immutable fields and no hidden
    state; and nested ``Labeled`` values (already checked when they were built,
    so they are not re-scanned). Iterative, with a depth limit, so
    attacker-controlled nesting cannot exhaust the stack.

    Raises:
        MutableValueError: if any part of ``value`` could change.
        NonFiniteNumberError: if a number is NaN or infinite.
        ValueTooDeepError: if nesting exceeds ``MAX_VALUE_DEPTH``.
    """
    pending: list[tuple[object, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_VALUE_DEPTH:
            raise ValueTooDeepError
        pending.extend((child, depth + 1) for child in _immutable_children(item))


def _immutable_children(item: object) -> Iterable[object]:
    """The parts of ``item`` still to check; raises if ``item`` itself is mutable."""
    item_type = type(item)
    if item_type in _IMMUTABLE_ATOMS:
        _check_finite(item, item_type)
        return ()
    if isinstance(item, Labeled):
        return ()
    if item_type in _TIMEZONE_AWARE:
        tzinfo = getattr(item, "tzinfo", None)
        if tzinfo is not None and type(tzinfo) not in _IMMUTABLE_TIMEZONES:
            raise MutableValueError(tzinfo)
        return ()
    if isinstance(item, enum.Enum):
        if set(vars(item)) - _ENUM_MEMBER_ATTRS:
            raise MutableValueError(item)  # state set on the member beyond its value
        return (item.value,)
    if item_type is tuple or item_type is frozenset or _is_slotless_tuple(item_type):
        return tuple(item)  # type: ignore[arg-type]
    if _is_frozen_dataclass(item):
        field_names = [field.name for field in dataclasses.fields(item)]  # type: ignore[arg-type]
        if set(getattr(item, "__dict__", {})) - set(field_names):
            raise MutableValueError(item)  # state hidden outside the declared fields
        return tuple(getattr(item, name) for name in field_names)
    raise MutableValueError(item)


def _typed_key(value: object) -> object:
    """Equality key that keeps types apart at every level: ``(1,)`` vs ``(True,)``.

    Called only on values that already passed ``_check_immutable``, so the
    nesting depth is bounded and every part is hashable.
    """
    value_type = type(value)
    if isinstance(value, Labeled):
        return (Labeled, getattr(value, "_key"), value.label)  # noqa: B009 - slot, no SLF
    if value_type is frozenset:
        return (value_type, frozenset(_typed_key(item) for item in value))  # type: ignore[attr-defined]
    if value_type is tuple or _is_slotless_tuple(value_type):
        return (value_type, tuple(_typed_key(item) for item in value))  # type: ignore[attr-defined]
    if _is_frozen_dataclass(value):
        return (
            value_type,
            tuple(
                _typed_key(getattr(value, field.name))
                for field in dataclasses.fields(value)  # type: ignore[arg-type]
            ),
        )
    return (value_type, value)


def _is_slotless_tuple(item_type: type) -> bool:
    """A tuple subclass that adds no instance state, such as a NamedTuple."""
    if not issubclass(item_type, tuple):
        return False
    return all(
        cls.__dict__.get("__slots__") == ()
        for cls in item_type.__mro__
        if cls not in {tuple, object}
    )


def _is_frozen_dataclass(item: object) -> bool:
    if not dataclasses.is_dataclass(item) or isinstance(item, type):
        return False
    params = getattr(type(item), "__dataclass_params__", None)
    return bool(getattr(params, "frozen", False))


@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class Labeled[T]:
    """A value together with its provenance label.

    The value must be immutable (see ``_check_immutable``), so it cannot change
    behind its label. There is no operator overloading: operations on labeled
    values go through the interpreter, which joins labels explicitly.

    Equality includes types at every level, so ``1``, ``True`` and ``1.0``
    with the same label stay distinct, also inside tuples and records
    (Python's ``1 == True == 1.0`` would merge them).
    """

    value: T
    label: Label
    _key: object = dataclasses.field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.label, Label):
            raise NotALabelError(self.label)
        _check_immutable(self.value)
        object.__setattr__(self, "_key", _typed_key(self.value))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Labeled):
            return NotImplemented
        return self._key == other._key and self.label == other.label

    @override
    def __hash__(self) -> int:
        return hash((self._key, self.label))

    def derive[U](self, value: U, *inputs: "Labeled[object]") -> "Labeled[U]":
        """A new value computed from this one and ``inputs``, labeled with all of them."""
        return Labeled(value, join_all(self.label, *(item.label for item in inputs)))


class NotALabelError(TypeError):
    """A labeled value was given something that is not a ``Label``."""

    def __init__(self, label: object) -> None:
        super().__init__(f"label must be a Label, got {_preview(label)}")
