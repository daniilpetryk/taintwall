# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Methods a plan may call: pure string methods, and ``record.get``.

A string method's result depends on the receiver and every argument, so it
carries all their deep labels. Sizes are checked before a result is built:
``replace`` and ``join`` compute the length first, and ``split`` the number
of parts, so a short plan cannot build a string or tuple past a limit.

``record.get(field, default)`` reads like ``record[field]``: the field's own
label joined with the record's and the key's, and the default's, which the
trace lists as an input either way. A missing field returns the default with
the same labels, since whether the field exists is the record's data.
"""

import ast
from collections.abc import Callable, Mapping
from typing import Final, cast

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.operators import apply
from taintwall.core.interpreter.trace import Result
from taintwall.core.interpreter.walk import deep_label, plain, unwrap
from taintwall.core.labels import Label, Labeled, join_all
from taintwall.core.values import Record

type Method = Callable[[ast.Call, str, tuple[object, ...], Guard], object]
"""A string method: receiver and plain arguments to its value."""


def _strings(node: ast.Call, values: tuple[object, ...], count: range) -> tuple[str, ...]:
    """The arguments, which must be strings, with an arity in ``count``."""
    if len(values) not in count or any(type(value) is not str for value in values):
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node)
    return values  # type: ignore[return-value]


def _lower(node: ast.Call, text: str, values: tuple[object, ...], _guard: Guard) -> object:
    _strings(node, values, range(1))
    return text.lower()


def _upper(node: ast.Call, text: str, values: tuple[object, ...], _guard: Guard) -> object:
    _strings(node, values, range(1))
    return text.upper()


def _strip(node: ast.Call, text: str, values: tuple[object, ...], _guard: Guard) -> object:
    return text.strip(*_strings(node, values, range(2)))


def _startswith(node: ast.Call, text: str, values: tuple[object, ...], _guard: Guard) -> object:
    (prefix,) = _strings(node, values, range(1, 2))
    return text.startswith(prefix)


def _endswith(node: ast.Call, text: str, values: tuple[object, ...], _guard: Guard) -> object:
    (suffix,) = _strings(node, values, range(1, 2))
    return text.endswith(suffix)


def _replace(node: ast.Call, text: str, values: tuple[object, ...], guard: Guard) -> object:
    old, new = _strings(node, values, range(2, 3))
    # count('') is len(text) + 1: an empty match before each character and at the end.
    # A wrong bound would still be caught by the built value's own size check; this
    # one only keeps a huge result from being built.
    growth = text.count(old) * (len(new) - len(old))
    guard.check_string_length(len(text) + growth)  # pragma: no mutate - see above
    return text.replace(old, new)


def _split(node: ast.Call, text: str, values: tuple[object, ...], _guard: Guard) -> object:
    # At most one part per character, and the string is already within its limit.
    separator = _strings(node, values, range(2))
    parts = cast("list[str]", apply(node, text.split, *separator))  # pragma: no mutate - type only
    return tuple(parts)


def _join(node: ast.Call, text: str, values: tuple[object, ...], guard: Guard) -> object:
    if len(values) != 1 or type(values[0]) is not tuple:
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node)
    items: tuple[object, ...] = values[0]
    if any(type(item) is not str for item in items):
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, "join")
    strings = cast("tuple[str, ...]", items)  # pragma: no mutate - type only
    # As in replace: the built value's size check backs this bound up.
    separators = len(text) * max(len(strings) - 1, 0)  # pragma: no mutate - see above
    length = sum(len(item) for item in strings) + separators  # pragma: no mutate - see above
    guard.check_string_length(length)
    return text.join(strings)


STRING_METHODS: Final[Mapping[str, Method]] = {
    "lower": _lower,
    "upper": _upper,
    "strip": _strip,
    "startswith": _startswith,
    "endswith": _endswith,
    "replace": _replace,
    "split": _split,
    "join": _join,
}
"""What each string method of ``syntax.STRING_METHODS`` does."""


def call_method(
    node: ast.Call,
    name: str,
    receiver: Labeled[object],
    arguments: tuple[Labeled[object], ...],
    guard: Guard,
) -> Result:
    """Call method ``name`` (checked by ``parse_plan``) on ``receiver``."""
    value, own = unwrap(receiver, guard)
    if name == "get" and isinstance(value, Record):
        return _get(node, value, own, arguments)
    method = STRING_METHODS.get(name)
    if method is None or type(value) is not str:
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    plain_arguments = tuple(plain(item.value, guard) for item in arguments)
    result = method(node, value, plain_arguments, guard)
    labels = (deep_label(item, guard) for item in arguments)
    return result, join_all(own, *labels)


def _get(
    node: ast.Call, record: Record, own: Label, arguments: tuple[Labeled[object], ...]
) -> Result:
    """``record.get(field)`` or ``record.get(field, default)``."""
    if len(arguments) not in {1, 2} or type(arguments[0].value) is not str:
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node, "get")
    key = arguments[0]
    default = arguments[1] if len(arguments) == 2 else Labeled(None, key.label)  # noqa: PLR2004 - field, default
    found = record.get(key.value)  # type: ignore[arg-type]
    field = default if found is None else found
    return field.value, join_all(field.label, own, key.label, default.label)
