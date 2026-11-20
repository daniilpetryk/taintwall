# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Types of tool parameters and results, small enough to check and to show.

A tool's parameters are checked before it runs (``calls``), and the planner
(stage 2, PR 4) reads the same types as Python signatures. Only what the
plan language can produce is expressible: atoms, a choice of atom values,
optional values, lists (plan tuples) and records with named fields. JSON schemas from benchmark
tool definitions translate into these; anything else is refused, so a tool
never advertises a parameter the checker cannot check.
"""

import dataclasses
import enum
from collections.abc import Mapping
from typing import Final


class Scalar(enum.Enum):
    """An atom type; ``ANY`` accepts every value."""

    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    NONE = "None"
    ANY = "object"


type Atom = str | int | float | bool | None


@dataclasses.dataclass(frozen=True, slots=True)
class OneOf:
    """One of these atom values: an enum or a literal."""

    values: tuple[Atom, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class Maybe:
    """A value of ``inner`` or ``None``."""

    inner: "ValueType"


@dataclasses.dataclass(frozen=True, slots=True)
class ListOf:
    """A list (a tuple in the plan) of ``item`` values."""

    item: "ValueType"


@dataclasses.dataclass(frozen=True, slots=True)
class RecordOf:
    """A record with these fields; ``name`` is how a signature shows it.

    Fields named in ``optional`` may be left out; the others must be present.
    """

    name: str
    fields: tuple[tuple[str, "ValueType"], ...]
    optional: frozenset[str] = frozenset()


type ValueType = Scalar | OneOf | Maybe | ListOf | RecordOf

_ATOMS: Final[Mapping[Scalar, tuple[type, ...]]] = {
    Scalar.STR: (str,),
    Scalar.INT: (int,),
    Scalar.FLOAT: (float, int),
    Scalar.BOOL: (bool,),
    Scalar.NONE: (type(None),),
}
"""Exact Python types of each atom; ``bool`` is never an ``int`` here."""

_JSON: Final[Mapping[str, Scalar]] = {
    "string": Scalar.STR,
    "integer": Scalar.INT,
    "number": Scalar.FLOAT,
    "boolean": Scalar.BOOL,
    "null": Scalar.NONE,
}


class UnsupportedSchemaError(ValueError):
    """A JSON schema uses something the tool types cannot express."""

    def __init__(self, what: str) -> None:
        super().__init__(f"unsupported JSON schema: {what}")


def accepts(expected: ValueType, value: object) -> bool:
    """Whether a plain Python value (lists, dicts, atoms) has type ``expected``."""
    match expected:
        case Scalar():
            return expected is Scalar.ANY or type(value) in _ATOMS[expected]
        case OneOf(values):
            # By type too: True is not 1 here.
            return any(type(value) is type(item) and value == item for item in values)
        case Maybe(inner):
            return value is None or accepts(inner, value)
        case ListOf(item):
            return type(value) is list and all(accepts(item, element) for element in value)
        case RecordOf(_, fields, optional):
            return type(value) is dict and all(
                accepts(field, value[name]) if name in value else name in optional
                for name, field in fields
            )


def render(expected: ValueType) -> str:
    """The type as a planner reads it in a signature."""
    match expected:
        case Scalar():
            return expected.value
        case OneOf(values):
            return f"Literal[{', '.join(repr(item) for item in values)}]"
        case Maybe(inner):
            return f"{render(inner)} | None"
        case ListOf(item):
            return f"list[{render(item)}]"
        case RecordOf(name, fields, _):
            # A record without declared fields is an open mapping.
            return name if fields else "dict"


def records_in(expected: ValueType) -> list[RecordOf]:
    """Record types with declared fields inside ``expected``, inner ones first."""
    match expected:
        case Maybe(inner) | ListOf(inner):
            return records_in(inner)
        case RecordOf(_, fields, _) if fields:
            nested = [record for _, field in fields for record in records_in(field)]
            return [*nested, expected]
        case _:
            return []


def render_record(record: RecordOf) -> str:
    """A record type as a planner reads it: a class with typed fields.

    A field that may be left out shows ``= ...``, as a field with a default.
    """
    fields = "".join(
        f"\n    {name}: {render(kind)}{' = ...' if name in record.optional else ''}"
        for name, kind in record.fields
    )
    return f"class {record.name}:{fields}"


type Schema = Mapping[str, object]

_DEFS: Final = "#/$defs/"
"""Where pydantic puts nested models; the only references followed."""


def from_json_schema(schema: Schema, defs: Mapping[str, Schema] | None = None) -> ValueType:
    """The tool type of a JSON schema: atoms, ``anyOf`` with null, arrays, objects.

    ``defs`` resolves ``{"$ref": "#/$defs/Name"}``, as pydantic writes nested
    models; by default the schema's own ``$defs``.
    """
    known = defs if defs is not None else _definitions(schema)
    reference = schema.get("$ref")
    if isinstance(reference, str):
        name = reference.removeprefix(_DEFS)
        if name == reference or name not in known:
            raise UnsupportedSchemaError(reference)
        return from_json_schema(known[name], known)
    if "anyOf" in schema:
        return _any_of(schema["anyOf"], known)
    if "enum" in schema or "const" in schema:
        return _one_of(schema["enum"] if "enum" in schema else [schema["const"]])
    return _typed(schema, known)


def _typed(schema: Schema, known: Mapping[str, Schema]) -> ValueType:
    """A schema by its ``type``: an atom, an array or an object."""
    kind = schema.get("type")
    if isinstance(kind, str) and kind in _JSON:
        return _JSON[kind]
    if kind == "array":
        items = schema.get("items")
        return ListOf(from_json_schema(_mapping(items), known) if items else Scalar.ANY)
    if kind == "object":
        return _object(schema, known)
    raise UnsupportedSchemaError(str(kind))


def _definitions(schema: Schema) -> Mapping[str, Schema]:
    """The schema's ``$defs``, each a schema."""
    defs = _mapping(schema.get("$defs", {}))
    return {str(name): _mapping(definition) for name, definition in defs.items()}


def _mapping(value: object) -> Schema:
    if not isinstance(value, Mapping):
        raise UnsupportedSchemaError(type(value).__name__)
    return value


def _any_of(options: object, defs: Mapping[str, Schema]) -> ValueType:
    """``anyOf`` of one type and ``null``: the only union a signature shows."""
    union = "anyOf"
    if not isinstance(options, list):
        raise UnsupportedSchemaError(union)
    kinds = [option for option in options if _mapping(option).get("type") != "null"]
    if len(kinds) != 1 or len(options) != 2:  # noqa: PLR2004 - one type and null
        raise UnsupportedSchemaError(union)
    return Maybe(from_json_schema(_mapping(kinds[0]), defs))


def _one_of(values: object) -> ValueType:
    """``enum`` or ``const``: atom values only, at least one."""
    choice = "enum"
    if not isinstance(values, list) or not values:
        raise UnsupportedSchemaError(choice)
    if not all(type(value) in _CHOICES for value in values):
        raise UnsupportedSchemaError(choice)
    return OneOf(tuple(values))


_CHOICES: Final = (str, int, float, bool, type(None))
_REQUIRED_KEY: Final = "required"


def _object(schema: Schema, defs: Mapping[str, Schema]) -> ValueType:
    """A record type; a bare ``object`` (a mapping of anything) has no fields."""
    properties = _mapping(schema.get("properties", {}))
    fields = tuple(
        (str(field), from_json_schema(_mapping(spec), defs)) for field, spec in properties.items()
    )
    required = schema.get("required", [])
    if not isinstance(required, list):
        raise UnsupportedSchemaError(_REQUIRED_KEY)
    optional = frozenset(name for name, _ in fields) - {str(name) for name in required}
    return RecordOf(str(schema.get("title", "Record")), fields, optional)
