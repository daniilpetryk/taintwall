# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Values across the tool boundary: plan values to Python and back.

Arguments leave the plan as plain Python (lists for tuples, dicts for
records). Results come back labeled: every atom, list element and record
field gets the result's label, so a field read later joins it with the
record's (Q2). Objects with ``model_dump`` (pydantic models) become records
keyed as their schemas name them (by alias),
enums their values, dates their ISO text. A key that is not a record field
name, an unknown type, or nesting past ``MAX_VALUE_DEPTH`` is refused rather
than guessed at.
"""

import datetime as dt
import enum
from collections.abc import Mapping, Sequence
from typing import Final

from taintwall.core.labels import MAX_VALUE_DEPTH, Label, Labeled
from taintwall.core.values import Record, is_field_name

_ATOMS: Final = (str, int, float, bool, type(None))


class ConversionError(TypeError):
    """A tool result cannot become a plan value: ``what`` it was, never its content."""

    def __init__(self, what: str) -> None:
        super().__init__(f"cannot convert a tool result: {what}")


_TOO_DEEP: Final = "nested too deep"
_BAD_KEY: Final = "a key that is not a field name"


def to_python(value: object) -> object:
    """A plan value as plain Python: labels dropped, tuples as lists, records as dicts."""
    while isinstance(value, Labeled):
        value = value.value
    if type(value) is tuple:
        return [to_python(item) for item in value]
    if isinstance(value, Record):
        return {name: to_python(field) for name, field in value.fields}
    return value


def to_labeled(value: object, label: Label) -> Labeled[object]:
    """A tool result as a plan value, every part of it labeled ``label``."""
    return _labeled(value, label, 0)


def _labeled(value: object, label: Label, depth: int) -> Labeled[object]:
    if depth > MAX_VALUE_DEPTH:
        raise ConversionError(_TOO_DEEP)
    value = _simple(value)
    if type(value) in _ATOMS:
        return Labeled(value, label)
    if isinstance(value, Mapping):
        fields = {_field_name(key): _labeled(item, label, depth + 1) for key, item in value.items()}
        return Labeled(Record.of(fields), label)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return Labeled(tuple(_labeled(item, label, depth + 1) for item in value), label)
    raise ConversionError(type(value).__name__)


def _simple(value: object) -> object:
    """Enums by value, dates as ISO text, models as their fields, named by alias."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True)
    return value


def _field_name(key: object) -> str:
    """A mapping key that can name a record field, as it is."""
    if not is_field_name(key):
        raise ConversionError(_BAD_KEY)
    return key  # type: ignore[return-value]
