# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Builtin functions of the plan language.

Labels follow what each result depends on:

* ``len`` and ``bool`` read only the container label: a count or emptiness
  does not depend on what the elements are.
* ``abs``, ``min``, ``max``, ``sum``, ``round``, ``any``, ``all`` and the
  conversions ``int``, ``float``, ``str`` read every nested element, so they
  carry deep labels, and they return an atom. As in the operators, ``bool``
  is not a number; ``sum``, ``any`` and ``all`` take a tuple; ``int`` and
  ``float`` parse only plain digits from text; ``str(None)`` is ``"None"``.
* Errors name the builtin (plan text), for the planner's retry.
* ``sorted`` keeps each element with its own label; the result's order
  depends on every element, so the tuple carries the deep label.
* ``range`` builds a tuple of integers labeled by its arguments; its length
  is checked before it is built.
"""

import ast
import math
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any, Final, cast

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.arguments import Arguments
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.operators import NUMBERS, apply
from taintwall.core.interpreter.trace import Result
from taintwall.core.interpreter.walk import deep_label, plain, truth
from taintwall.core.labels import Labeled, join_all
from taintwall.core.values import ATOMS

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

_INTEGER: Final = re.compile(r"[+-]?[0-9]+")
_DECIMAL: Final = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?")
"""What ``int`` and ``float`` parse from text: plain digits, no underscores or spaces."""

_DIGITS_PER_BIT: Final = math.log10(2)

_CONVERSIONS: Final[Mapping[str, type]] = {"int": int, "float": float, "str": str}
"""Conversions between atoms; containers are not converted."""


def _arity(node: ast.Call, name: str, args: tuple[object, ...], accepted: range) -> None:
    if len(args) not in accepted:
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node, name)


def _number(value: object) -> bool:
    """A number for arithmetic: ``bool`` is not one, as in the operators."""
    return type(value) in NUMBERS


def _accepts(name: str, values: tuple[object, ...]) -> bool:
    """Argument types: numbers for abs, round and sum; a tuple for sum, any and all."""
    first = values[0]
    if name in {"abs", "round"}:
        return _number(first) and all(type(digits) is int for digits in values[1:])
    if name == "sum":
        return type(first) is tuple and all(_number(item) for item in first)
    if name in {"any", "all"}:
        return type(first) is tuple
    return True


def _atomic(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    function, accepted = _ATOMIC[name]
    _arity(node, name, args, accepted)
    values = tuple(plain(item.value, guard) for item in args)
    if not _accepts(name, values):
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    value = apply(node, function, *values, detail=name)
    if type(value) not in ATOMS:
        # min/max over sequences of containers: results must be atoms.
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    return value, join_all(*(deep_label(item, guard) for item in args))


def _length(node: ast.Call, name: str, args: tuple[Labeled[object], ...], _guard: Guard) -> Result:
    # The length is the container's, not its elements', so no nested walk.
    _arity(node, name, args, range(1, 2))
    return apply(node, len, args[0].value, detail=name), args[0].label


def _bool(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    _arity(node, name, args, range(1, 2))
    return truth(args[0], guard)


def _convert(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    _arity(node, name, args, range(1, 2))
    value = plain(args[0].value, guard)
    if not _convertible(name, value):
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    # _digits is a lower bound: past it Python refuses too (as a type error).
    limit = sys.get_int_max_str_digits()
    too_long = type(value) is int and _digits(value) > limit  # pragma: no mutate - bound
    if too_long:
        raise error_at(PlanErrorCode.VALUE_TOO_LARGE, node, name)
    return apply(node, _CONVERSIONS[name], value, detail=name), deep_label(args[0], guard)


def _convertible(name: str, value: object) -> bool:
    """``str`` takes any atom (``str(None)`` is "None", as in an f-string).

    ``int`` and ``float`` take numbers, not ``bool``, and text that is plainly
    a number: Python's own parsers also accept underscores and surrounding
    spaces, which text from untrusted data should not get away with.
    """
    if name == "str":
        return type(value) in ATOMS
    if type(value) is str:
        # int() itself rejects decimal text, so the choice only sharpens the error.
        pattern = _INTEGER if name == "int" else _DECIMAL  # pragma: no mutate - see above
        return pattern.fullmatch(value) is not None
    return _number(value)


def _digits(value: int) -> int:
    """At least the number of decimal digits of ``value``, from its size alone."""
    return int(abs(value).bit_length() * _DIGITS_PER_BIT)


def _range(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    _arity(node, name, args, range(1, 4))
    bounds = tuple(item.value for item in args)
    if any(type(bound) is not int for bound in bounds):
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    steps = cast("range", apply(node, range, *bounds, detail=name))  # pragma: no mutate - type only
    # A range past a C ssize_t has no len(): that is too large, typed like the rest.
    count = apply(node, len, steps, detail=name)
    guard.check_items(cast("int", count))  # pragma: no mutate - type only
    return tuple(steps), join_all(*(item.label for item in args))


def _sorted(node: ast.Call, name: str, args: tuple[Labeled[object], ...], guard: Guard) -> Result:
    _arity(node, name, args, range(1, 2))
    items = args[0].value
    if type(items) is not tuple:
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    keys: list[Any] = [plain(item, guard) for item in items]
    by_key = lambda: sorted(range(len(keys)), key=keys.__getitem__)  # noqa: E731 - one use
    order = cast("list[int]", apply(node, by_key, detail=name))  # pragma: no mutate - type only
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
