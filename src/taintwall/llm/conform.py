# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Which JSON schemas a client can promise, and whether a reply follows one.

Providers that take ``strict`` enforce the schema, but a reply is checked
here too, so ``LLMClient``'s promise holds whatever served it and the cache
never stores a reply that does not fit. ``supported`` says whether a schema
uses only what ``conforms`` can check: ``type`` (object, array, string,
integer, number, boolean, null), ``properties``, ``required``,
``additionalProperties``, ``items``, ``enum``, ``const``, ``anyOf``, and the
annotations ``title``, ``description``, ``default``, ``examples``,
``deprecated`` and ``$comment``, which describe and never constrain. A client
refuses any other schema before it calls (``$ref`` included), so nobody pays
for a reply that could never be accepted.
"""

from collections.abc import Mapping, Sequence
from typing import Final

_ANNOTATIONS: Final = frozenset(
    {"title", "description", "default", "examples", "deprecated", "$comment"}
)
_CHECKED: Final = frozenset(
    {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf"}
)

_TYPES: Final[Mapping[str, tuple[type, ...]]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}
"""Exact Python types of each JSON type; a ``bool`` is never a number."""

_KINDS: Final = frozenset({*_TYPES, "object", "array"})


def supported(schema: object) -> bool:
    """Whether ``conforms`` can check every keyword of ``schema``, nested ones too."""
    if not isinstance(schema, Mapping) or not set(schema) <= _CHECKED | _ANNOTATIONS:
        return False
    kind = schema.get("type")
    properties = schema.get("properties", {})
    options = schema.get("anyOf", [])
    additional = schema.get("additionalProperties", True)
    return (
        (kind is None or (isinstance(kind, str) and kind in _KINDS))
        and isinstance(properties, Mapping)
        and all(map(supported, properties.values()))
        and isinstance(schema.get("required", []), list)
        and isinstance(schema.get("enum", []), list)
        and isinstance(options, list)
        and all(map(supported, options))
        and ("items" not in schema or supported(schema["items"]))
        and (isinstance(additional, bool) or supported(additional))
    )


def conforms(value: object, schema: object) -> bool:
    """Whether ``value`` (parsed JSON) follows ``schema``; ``False`` if it is unsupported."""
    if not supported(schema):
        return False
    assert isinstance(schema, Mapping)  # noqa: S101 - supported() guarantees it
    return _fits(value, schema)


def _fits(value: object, schema: Mapping[str, object]) -> bool:
    return _allowed(value, schema) and _typed(value, schema)


def _allowed(value: object, schema: Mapping[str, object]) -> bool:
    """``const``, ``enum`` and ``anyOf``: each holds when absent."""
    enum: list[object] = schema.get("enum", [value])  # type: ignore[assignment]
    options: list[Mapping[str, object]] = schema.get("anyOf", [schema])  # type: ignore[assignment]
    return (
        ("const" not in schema or _same(value, schema["const"]))
        and any(_same(value, item) for item in enum)
        and ("anyOf" not in schema or any(_fits(value, option) for option in options))
    )


def _typed(value: object, schema: Mapping[str, object]) -> bool:
    """``type``, with ``properties`` and ``items`` below it; any type when absent."""
    kind = schema.get("type")
    if kind == "object":
        return _object(value, schema)
    if kind == "array":
        items: Mapping[str, object] | None = schema.get("items")  # type: ignore[assignment]
        return type(value) is list and (items is None or all(_fits(item, items) for item in value))
    return kind is None or type(value) in _TYPES[str(kind)]


def _same(value: object, expected: object) -> bool:
    """Equal and of the same type: ``True`` is not ``1``."""
    return type(value) is type(expected) and value == expected


def _object(value: object, schema: Mapping[str, object]) -> bool:
    properties: Mapping[str, Mapping[str, object]] = schema.get("properties", {})  # type: ignore[assignment]
    required: Sequence[str] = schema.get("required", [])  # type: ignore[assignment]
    if type(value) is not dict or not set(required) <= set(value):
        return False
    additional = schema.get("additionalProperties", True)
    extra = [name for name in value if name not in properties]
    if additional is False and extra:
        return False
    if isinstance(additional, Mapping) and not all(
        _fits(value[name], additional) for name in extra
    ):
        return False
    return all(_fits(item, properties[name]) for name, item in value.items() if name in properties)
