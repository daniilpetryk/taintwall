# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Which values may carry a label: immutable ones, checked by exact type.

A value must not be able to change behind its label. Accepted, by exact type:
finite immutable atoms; tuples (and NamedTuples) and frozensets of immutable
values; datetimes and times that are naive or use a fixed offset or
``ZoneInfo``; enum members with an immutable value and no extra attributes;
frozen dataclasses with immutable fields and no hidden state. Values of an
already-checked type (``Labeled``, passed in by the caller) are not re-scanned.
The walk is iterative with a depth limit, so attacker-controlled nesting
cannot exhaust the stack.
"""

import cmath
import dataclasses
import datetime as dt
import enum
import fractions
import math
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from taintwall.core.labels.errors import (
    MutableValueError,
    NonFiniteNumberError,
    ValueTooDeepError,
)

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


def check_immutable(value: object, checked: type) -> None:
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
        if isinstance(item, checked):
            continue  # checked when it was built
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


def _timezone_children(item: object) -> tuple[object, ...]:
    _check_timezone(item)
    return ()


def _sequence_children(item: object) -> tuple[object, ...]:
    assert isinstance(item, tuple | frozenset)  # noqa: S101 - the rule checked the type
    return tuple(item)


def _is_plain_sequence(item: object) -> bool:
    item_type = type(item)
    return item_type is tuple or item_type is frozenset or is_slotless_tuple(item_type)


def is_slotless_tuple(item_type: type) -> bool:
    """A tuple subclass that adds no instance state, such as a NamedTuple."""
    if not issubclass(item_type, tuple):
        return False
    return all(
        cls.__dict__.get("__slots__") == ()
        for cls in item_type.__mro__
        if cls not in {tuple, object}
    )


def is_frozen_dataclass(item: object) -> bool:
    """A frozen dataclass instance (not the class itself)."""
    if isinstance(item, type) or not dataclasses.is_dataclass(item):
        return False
    params = getattr(type(item), "__dataclass_params__")  # noqa: B009 - not in the type stubs
    return bool(params.frozen)


type _Rule = tuple[Callable[[object], bool], Callable[[object], Iterable[object]]]

_CONTAINER_RULES: Final[tuple[_Rule, ...]] = (
    (lambda item: type(item) in _TIMEZONE_AWARE, _timezone_children),
    (lambda item: isinstance(item, enum.Enum), _enum_children),
    (_is_plain_sequence, _sequence_children),
    (is_frozen_dataclass, _dataclass_children),
)
"""How to accept each non-atom kind of value, in order: (accepts, children)."""
