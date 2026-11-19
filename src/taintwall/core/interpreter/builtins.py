# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Builtin functions of the plan language.

Labels follow what each result depends on:

* ``len`` and ``bool`` read only the container label: a count or emptiness
  does not depend on what the elements are.
* ``abs``, ``min``, ``max``, ``sum``, ``round``, ``any``, ``all`` and the
  conversions ``int``, ``float``, ``str`` read every nested element, so they
  carry deep labels, and they return an atom.
* ``sorted`` keeps each element with its own label; the result's order
  depends on every element, so the tuple carries the deep label.
* ``range`` builds a tuple of integers labeled by its arguments; its length
  is checked before it is built.
"""

import ast
from collections.abc import Callable, Mapping
from typing import Any, Final, cast

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.arguments import Arguments
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.operators import ATOMS, apply
from taintwall.core.interpreter.trace import Result
from taintwall.core.interpreter.walk import deep_label, plain, truth
from taintwall.core.labels import Labeled, join_all

type Builtin = Callable[[ast.Call, str, tuple[Labeled[object], ...], Guard], Result]

_ANY: Final = 2**31
"""Upper bound of an arity that is not limited (a call cannot have more arguments)."""

_ATOMIC: Final[Mapping[str, tuple[Callable[..., object], range]]] = {
    "abs": (abs, range(1, 2)),
    "min": (min, range(1, _ANY)),
    "max": (max, range(1, _ANY)),
    "sum": (sum, range(1, 2)),
    "round": (round, range(1, 3)),
    "any": (any, range(1, 2)),
    "all": (all, range(1, 2)),
}
"""Builtins over plain values that return an atom, with the arities they accept."""

_CONVERSIONS: Final[Mapping[str, type]] = {"int": int, "float": float, "str": str}
"""Conversions between atoms; containers are not converted."""


def _arity(node: ast.Call, name: str, args: tuple[object, ...], accepted: range) -> None:
    if len(args) not in accepted:
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node, name)


def _atomic(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    function, accepted = _ATOMIC[name]
    _arity(node, name, args, accepted)
    value = apply(node, function, *(plain(item.value, guard) for item in args))
    if type(value) not in ATOMS:
        # min/max over sequences of containers: results must be atoms.
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    return value, join_all(*(deep_label(item, guard) for item in args))


def _length(node: ast.Call, name: str, args: tuple[Labeled[object], ...], _guard: Guard) -> Result:
    # The length is the container's, not its elements', so no nested walk.
    _arity(node, name, args, range(1, 2))
    return apply(node, len, args[0].value), args[0].label


def _bool(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    _arity(node, name, args, range(1, 2))
    return truth(args[0], guard)


def _convert(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    _arity(node, name, args, range(1, 2))
    value = plain(args[0].value, guard)
    if type(value) not in ATOMS or value is None:
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    # Number parsing is bounded by the string limit and Python's digit limit.
    return apply(node, _CONVERSIONS[name], value), deep_label(args[0], guard)


def _range(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    _arity(node, name, args, range(1, 4))
    bounds = tuple(item.value for item in args)
    if any(type(bound) is not int for bound in bounds):
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    steps = cast("range", apply(node, range, *bounds))  # pragma: no mutate - type only
    guard.check_items(len(steps))
    return tuple(steps), join_all(*(item.label for item in args))


def _sorted(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    _arity(node, name, args, range(1, 2))
    items = args[0].value
    if type(items) is not tuple:
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    keys: list[Any] = [plain(item, guard) for item in items]
    by_key = lambda: sorted(range(len(keys)), key=keys.__getitem__)  # noqa: E731 - one use
    order = cast("list[int]", apply(node, by_key))  # pragma: no mutate - type only
    ordered = tuple(items[index] for index in order)
    return ordered, deep_label(args[0], guard)


_HANDLERS: Final[Mapping[str, Builtin]] = {
    "len": _length,
    "bool": _bool,
    "range": _range,
    "sorted": _sorted,
    **dict.fromkeys(_ATOMIC, _atomic),
    **dict.fromkeys(_CONVERSIONS, _convert),
}

BUILTINS: Final = frozenset(_HANDLERS)
"""Functions every plan can call besides tools."""


def call_builtin(node: ast.Call, name: str, arguments: Arguments, guard: Guard) -> Result:
    """Call builtin ``name``; builtins take positional arguments only."""
    if arguments.keyword:
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node, name)
    return _HANDLERS[name](node, name, arguments.positional, guard)
