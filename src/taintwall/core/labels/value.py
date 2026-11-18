# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Labeled values: an immutable value together with its label.

Values must be immutable, checked by exact type and with a depth limit, so a
value cannot change behind its label. Equality keeps types apart at every
level, so ``1``, ``True`` and ``1.0`` stay distinct.
"""

import cmath
import dataclasses
import datetime as dt
import enum
import fractions
import math
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Final, override
from zoneinfo import ZoneInfo

from taintwall.core.labels.errors import (
    MutableValueError,
    NonFiniteNumberError,
    NotALabelError,
    ValueTooDeepError,
)
from taintwall.core.labels.label import Label, join_all

MAX_VALUE_DEPTH: Final = 64
"""Deepest nesting of tuples, frozensets and records accepted in a labeled value."""


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
            raise ValueTooDeepError(MAX_VALUE_DEPTH)
        pending.extend((child, depth + 1) for child in _immutable_children(item))


def _immutable_children(item: object) -> Iterable[object]:
    """The parts of ``item`` still to check; raises if ``item`` itself is mutable."""
    item_type = type(item)
    if item_type in _IMMUTABLE_ATOMS:
        _check_finite(item, item_type)
        return ()
    for accepts, children in _CONTAINER_RULES:
        if accepts(item):
            return children(item)
    raise MutableValueError(item)


def _check_timezone(item: object) -> None:
    assert isinstance(item, dt.datetime | dt.time)  # noqa: S101 - the rule checked the type
    tzinfo = item.tzinfo
    if tzinfo is not None and type(tzinfo) not in _IMMUTABLE_TIMEZONES:
        raise MutableValueError(tzinfo)


def _enum_children(item: object) -> tuple[object, ...]:
    assert isinstance(item, enum.Enum)  # noqa: S101 - the rule checked the type
    if set(vars(item)) - _ENUM_MEMBER_ATTRS:
        raise MutableValueError(item)  # state set on the member beyond its value
    return (item.value,)


def _dataclass_children(item: object) -> tuple[object, ...]:
    field_names = [field.name for field in dataclasses.fields(item)]  # type: ignore[arg-type]
    if set(getattr(item, "__dict__", {})) - set(field_names):
        raise MutableValueError(item)  # state hidden outside the declared fields
    return tuple(getattr(item, name) for name in field_names)


def _no_children(_item: object) -> tuple[object, ...]:
    return ()


def _timezone_children(item: object) -> tuple[object, ...]:
    _check_timezone(item)
    return ()


def _sequence_children(item: object) -> tuple[object, ...]:
    assert isinstance(item, tuple | frozenset)  # noqa: S101 - the rule checked the type
    return tuple(item)


def _is_plain_sequence(item: object) -> bool:
    item_type = type(item)
    return item_type is tuple or item_type is frozenset or _is_slotless_tuple(item_type)


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
    """A frozen dataclass instance (not the class itself)."""
    if isinstance(item, type) or not dataclasses.is_dataclass(item):
        return False
    params = getattr(type(item), "__dataclass_params__")  # noqa: B009 - not in the type stubs
    return bool(params.frozen)


type _Rule = tuple[Callable[[object], bool], Callable[[object], Iterable[object]]]

_CONTAINER_RULES: Final[tuple[_Rule, ...]] = (
    (lambda item: isinstance(item, Labeled), _no_children),  # checked when it was built
    (lambda item: type(item) in _TIMEZONE_AWARE, _timezone_children),
    (lambda item: isinstance(item, enum.Enum), _enum_children),
    (_is_plain_sequence, _sequence_children),
    (_is_frozen_dataclass, _dataclass_children),
)
"""How to accept each non-atom kind of value, in order: (accepts, children)."""


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
