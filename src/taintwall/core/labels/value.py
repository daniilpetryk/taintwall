# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Labeled values: an immutable value together with its label.

Which values are accepted is decided in ``immutability``. Equality keeps
types apart at every level, so ``1``, ``True`` and ``1.0`` stay distinct.
"""

import dataclasses
from typing import override

from taintwall.core.labels.errors import NotALabelError
from taintwall.core.labels.immutability import (
    check_immutable,
    is_frozen_dataclass,
    is_slotless_tuple,
)
from taintwall.core.labels.label import Label


def _typed_key(value: object) -> object:
    """Equality key that keeps types apart at every level: ``(1,)`` vs ``(True,)``.

    Called only on values that already passed ``check_immutable``, so the
    nesting depth is bounded and every part is hashable.
    """
    value_type = type(value)
    if isinstance(value, Labeled):
        return (Labeled, getattr(value, "_key"), value.label)  # noqa: B009 - slot, no SLF
    if value_type is frozenset:
        return (value_type, frozenset(_typed_key(item) for item in value))  # type: ignore[attr-defined]
    if value_type is tuple or is_slotless_tuple(value_type):
        return (value_type, tuple(_typed_key(item) for item in value))  # type: ignore[attr-defined]
    if is_frozen_dataclass(value):
        return (
            value_type,
            tuple(
                _typed_key(getattr(value, field.name))
                for field in dataclasses.fields(value)  # type: ignore[arg-type]
            ),
        )
    return (value_type, value)


@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class Labeled[T]:
    """A value together with its provenance label.

    The value must be immutable (see ``immutability.check_immutable``), so it cannot change
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
        check_immutable(self.value, checked=Labeled)
        object.__setattr__(self, "_key", _typed_key(self.value))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Labeled):
            return NotImplemented
        return self._key == other._key and self.label == other.label

    @override
    def __hash__(self) -> int:
        return hash((self._key, self.label))
