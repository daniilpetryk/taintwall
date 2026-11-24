# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Whether a reply follows the JSON schema a request asked for.

Providers that take ``strict`` enforce the schema, but a reply is checked
here too, so ``LLMClient``'s promise holds whatever served it and the cache
never stores a reply that does not fit. The keywords are the ones strict
structured output allows: ``type`` (object, array, string, integer, number,
boolean, null), ``properties``, ``required``, ``additionalProperties: false``
and ``items``. Any other keyword makes the schema unknown, and nothing fits
it: a client cannot promise what it cannot check.
"""

from collections.abc import Mapping
from typing import Final

_KNOWN: Final = frozenset({"type", "properties", "required", "additionalProperties", "items"})

_TYPES: Final[Mapping[str, tuple[type, ...]]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}
"""Exact Python types of each JSON type; a ``bool`` is never a number."""


def conforms(value: object, schema: object) -> bool:
    """Whether ``value`` (parsed JSON) follows ``schema``."""
    if not isinstance(schema, Mapping) or not set(schema) <= _KNOWN:
        return False
    kind = schema.get("type")
    if kind == "object":
        return _object(value, schema)
    if kind == "array":
        items = schema.get("items")
        return type(value) is list and all(conforms(item, items) for item in value)
    return isinstance(kind, str) and kind in _TYPES and type(value) in _TYPES[kind]


def _object(value: object, schema: Mapping[str, object]) -> bool:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if type(value) is not dict or not isinstance(properties, Mapping):
        return False
    if not isinstance(required, list) or not set(required) <= set(value):
        return False
    if schema.get("additionalProperties", True) is not True and not set(value) <= set(properties):
        return False
    return all(
        conforms(item, properties[name]) for name, item in value.items() if name in properties
    )
