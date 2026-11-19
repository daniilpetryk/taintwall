# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Operators of the plan language: type rules, values and result labels.

* Arithmetic works on numbers only (no printf-style ``%`` on strings).
* ``+`` also joins two strings or two tuples, ``*`` repeats a string or tuple
  by an integer; tuples keep per-element labels, and the result's own label
  is the operands' own labels. Sizes are checked before building.
* ``not`` reads only the top level, like ``len``; ``-`` and ``+`` need numbers.
* Comparisons read every nested element, so they carry deep labels.

Each function returns ``(value, label)``; the evaluator records the trace.
"""

import ast
import operator
from collections.abc import Callable, Mapping, Sequence
from typing import Final

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.trace import Result
from taintwall.core.interpreter.walk import deep_label, plain, truth
from taintwall.core.labels import Labeled, join_all

NUMBERS: Final = frozenset({int, float})
"""Types arithmetic accepts (not ``bool``)."""

ATOMS: Final = frozenset({str, int, float, bool, type(None)})
"""Types an f-string may interpolate and a builtin may return."""

_ARITHMETIC: Final[Mapping[type[ast.operator], Callable[..., object]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_SIGN: Final[Mapping[type[ast.unaryop], Callable[..., object]]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_COMPARE: Final[Mapping[type[ast.cmpop], Callable[..., object]]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda left, right: operator.contains(right, left),
    ast.NotIn: lambda left, right: not operator.contains(right, left),
    # parse_plan allows identity only against None.
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}


def apply(
    node: ast.expr, function: Callable[..., object], *args: object, detail: str = ""
) -> object:
    """Call ``function``, turning Python errors into typed plan errors.

    ``detail`` names what was called (plan text), for the planner's retry.
    """
    try:
        return function(*args)
    except ZeroDivisionError:
        raise error_at(PlanErrorCode.DIVISION_BY_ZERO, node, detail) from None
    except OverflowError:
        raise error_at(PlanErrorCode.VALUE_TOO_LARGE, node, detail) from None
    except (TypeError, ValueError):
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, detail) from None


def binary(node: ast.BinOp, left: Labeled[object], right: Labeled[object], guard: Guard) -> Result:
    """``left <op> right``."""
    a, b = left.value, right.value
    label = left.label.join(right.label)
    if type(a) in NUMBERS and type(b) in NUMBERS:
        return apply(node, _ARITHMETIC[type(node.op)], a, b), label
    if isinstance(node.op, ast.Add) and type(a) is type(b) and type(a) in {str, tuple}:
        # At most twice the size limit, so the check after building is enough.
        return a + b, label  # type: ignore[operator]
    if isinstance(node.op, ast.Mult):
        sequence, count = (a, b) if type(b) is int else (b, a)
        if type(count) is int and type(sequence) in {str, tuple}:
            guard.check_repeat(sequence, count)  # type: ignore[arg-type]
            return sequence * count, label  # type: ignore[operator]
    raise error_at(PlanErrorCode.TYPE_MISMATCH, node, type(node.op).__name__)


def unary(node: ast.UnaryOp, operand: Labeled[object], guard: Guard) -> Result:
    """``<op> operand``."""
    inner = operand.value
    if isinstance(node.op, ast.Not):
        # Truthiness depends on emptiness only (``Record`` has ``len``), like ``len``.
        holds, label = truth(operand, guard)
        return not holds, label
    if type(inner) in NUMBERS:
        # Negating a finite int or float cannot fail.
        return _SIGN[type(node.op)](inner), operand.label
    raise error_at(PlanErrorCode.TYPE_MISMATCH, node, type(node.op).__name__)


def compare(node: ast.Compare, operands: Sequence[Labeled[object]], guard: Guard) -> Result:
    """A comparison chain such as ``a < b <= c``; every operand is evaluated."""
    result = True
    # One operator between each pair of operands, so the lengths always match.
    for op, left, right in zip(node.ops, operands, operands[1:], strict=False):  # pragma: no mutate
        check = _COMPARE[type(op)]
        result = result and bool(
            apply(node, check, plain(left.value, guard), plain(right.value, guard))
        )
    return result, join_all(*(deep_label(item, guard) for item in operands))
