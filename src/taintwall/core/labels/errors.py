# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Errors of the label model.

Every message previews offending input through a bounded ``reprlib``, so a
hostile value can neither flood a log nor replace the typed error with an
exception from its own ``__repr__``.
"""

import enum
import reprlib
from typing import Final

_ERROR_PREVIEW: Final = 64
_REPR: Final = reprlib.Repr(maxstring=_ERROR_PREVIEW, maxother=_ERROR_PREVIEW)


def preview(value: object) -> str:
    """Bounded repr for error messages.

    ``reprlib`` caps the size and falls back to ``<Type instance at ...>`` when
    an object's own ``__repr__`` raises, so a hostile value cannot replace our
    typed error with its own exception.
    """
    return _REPR.repr(value)[:_ERROR_PREVIEW]


class InvalidSourceError(ValueError):
    """A source name is malformed, or does not fit its kind."""

    def __init__(self, kind: object, name: object) -> None:
        super().__init__(f"invalid source name for {preview(kind)}: {preview(name)}")


class InvalidKindError(TypeError):
    """A source kind is not a ``SourceKind`` member."""

    def __init__(self, kind: object) -> None:
        super().__init__(f"source kind must be a SourceKind, got {preview(kind)}")


class InvalidReadersError(TypeError):
    """Label readers are not a ``Readers`` member."""

    def __init__(self, readers: object) -> None:
        super().__init__(f"readers must be a Readers member, got {preview(readers)}")


class NotASourceError(TypeError):
    """A label was given something that is not a ``Source``."""

    def __init__(self, item: object) -> None:
        super().__init__(f"label sources must be Source objects, got {preview(item)}")


class UnmintedSourceError(PermissionError):
    """A trusted source was created outside ``taintwall.core.mint``."""

    def __init__(self, kind: enum.StrEnum) -> None:
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
        super().__init__(f"labeled numbers must be finite, got {preview(value)}")


class ValueTooDeepError(ValueError):
    """A labeled value is nested deeper than ``MAX_VALUE_DEPTH``."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"labeled value is nested deeper than {limit} levels")


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


class NotALabelError(TypeError):
    """A labeled value was given something that is not a ``Label``."""

    def __init__(self, label: object) -> None:
        super().__init__(f"label must be a Label, got {preview(label)}")
