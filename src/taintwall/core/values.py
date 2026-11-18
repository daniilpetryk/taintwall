# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Structured values with a label per field (decision Q2).

A tool response or a Q-LLM answer is a ``Record``: named fields, each its own
``Labeled`` value. Reading a field joins the field's label with the label of
the record itself, so a trusted balance and an untrusted token name returned
together stay apart until a plan combines them.
"""

import dataclasses
import re
from collections.abc import Mapping
from typing import Final, Self, override

from taintwall.core.labels import Labeled

_FIELD_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")


class InvalidFieldError(ValueError):
    """A record field name is not a short lowercase identifier."""

    def __init__(self, name: object) -> None:
        super().__init__(f"invalid record field name: {name!r:.64}")


class NotLabeledFieldError(TypeError):
    """A record field value is not a ``Labeled`` value."""

    def __init__(self, name: str) -> None:
        super().__init__(f"record field {name} must be a Labeled value")


@dataclasses.dataclass(frozen=True, slots=True)
class Record:
    """Immutable named fields, each a ``Labeled`` value.

    Field names are lowercase identifiers (they appear in plans as
    ``record.field``), kept sorted so equal records compare equal.
    """

    fields: tuple[tuple[str, Labeled[object]], ...]

    def __post_init__(self) -> None:
        names = [name for name, _ in self.fields]
        for name, value in self.fields:
            if type(name) is not str or not _FIELD_NAME.fullmatch(name):
                raise InvalidFieldError(name)
            if not isinstance(value, Labeled):
                raise NotLabeledFieldError(name)
        if len(set(names)) != len(names):
            raise InvalidFieldError(names)
        object.__setattr__(self, "fields", tuple(sorted(self.fields, key=lambda item: item[0])))

    @classmethod
    def of(cls, values: Mapping[str, Labeled[object]]) -> Self:
        """A record from a mapping of field names to labeled values."""
        return cls(tuple(values.items()))

    def get(self, name: str) -> Labeled[object] | None:
        """The field called ``name``, or ``None`` if there is no such field."""
        for field_name, value in self.fields:
            if field_name == name:
                return value
        return None

    @property
    def names(self) -> tuple[str, ...]:
        """Field names in sorted order."""
        return tuple(name for name, _ in self.fields)

    @override
    def __repr__(self) -> str:
        return f"Record({', '.join(self.names)})"
