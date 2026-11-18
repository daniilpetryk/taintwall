# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Operators, boolean logic and calls, as syntax-node handlers.

The rules for values and labels live in ``operators``, ``builtins`` and
``tools``; these handlers evaluate operands and record the trace.

* ``and``/``or`` short-circuit, and the result carries every evaluated
  operand, since the left one chose the outcome.
* A call resolves its name before its arguments run, as Python does, so a
  misspelled outer call fails before inner calls with side effects.
"""

import ast

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.arguments import Arguments
from taintwall.core.interpreter.builtins import BUILTINS, call_builtin
from taintwall.core.interpreter.context import Context
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.operators import binary, compare, unary
from taintwall.core.interpreter.trace import EventKind, Origin, Ref
from taintwall.core.interpreter.walk import deep_label, truthy
from taintwall.core.labels import join_all


def binary_op(context: Context, node: ast.expr) -> Ref:
    """``a + b`` and the other arithmetic operators."""
    assert isinstance(node, ast.BinOp)  # noqa: S101 - dispatch guarantees the type
    left, right = context.expr(node.left), context.expr(node.right)
    result = binary(node, left.labeled, right.labeled, context.guard)
    origin = Origin(EventKind.OPERATION, node, type(node.op).__name__, (left.id, right.id))
    return context.new(result, origin)


def unary_op(context: Context, node: ast.expr) -> Ref:
    """``-a``, ``+a`` and ``not a``."""
    assert isinstance(node, ast.UnaryOp)  # noqa: S101 - dispatch guarantees the type
    operand = context.expr(node.operand)
    origin = Origin(EventKind.OPERATION, node, type(node.op).__name__, (operand.id,))
    return context.new(unary(node, operand.labeled), origin)


def comparison(context: Context, node: ast.expr) -> Ref:
    """``a < b <= c``, ``a in b`` and the other comparisons."""
    assert isinstance(node, ast.Compare)  # noqa: S101 - dispatch guarantees the type
    refs = [context.expr(node.left), *(context.expr(item) for item in node.comparators)]
    result = compare(node, [ref.labeled for ref in refs], context.guard)
    origin = Origin(EventKind.OPERATION, node, "Compare", tuple(ref.id for ref in refs))
    return context.new(result, origin)


def boolean(context: Context, node: ast.expr) -> Ref:
    """``a and b`` and ``a or b``."""
    assert isinstance(node, ast.BoolOp)  # noqa: S101 - dispatch guarantees the type
    stop_on_true = isinstance(node.op, ast.Or)
    evaluated: list[Ref] = []
    for operand in node.values:
        evaluated.append(context.expr(operand))
        if truthy(evaluated[-1].labeled.value) is stop_on_true:
            break
    label = join_all(*(deep_label(ref.labeled, context.guard, node) for ref in evaluated))
    chosen = evaluated[-1].labeled.value
    origin = Origin(
        EventKind.OPERATION, node, type(node.op).__name__, tuple(ref.id for ref in evaluated)
    )
    return context.new((chosen, label), origin)


def call(context: Context, node: ast.expr) -> Ref:
    """``tool(...)`` or ``builtin(...)``."""
    assert isinstance(node, ast.Call)  # noqa: S101 - dispatch guarantees the type
    assert isinstance(node.func, ast.Name)  # noqa: S101 - parse_plan guarantees it
    function = node.func.id
    is_tool = function in context.tools
    if not is_tool and function not in BUILTINS:
        raise error_at(PlanErrorCode.UNKNOWN_TOOL, node, function)
    positional = [context.expr(arg) for arg in node.args]
    keyword = {kw.arg: context.expr(kw.value) for kw in node.keywords if kw.arg is not None}
    arguments = Arguments(
        tuple(ref.labeled for ref in positional),
        {key: ref.labeled for key, ref in keyword.items()},
    )
    inputs = tuple(ref.id for ref in [*positional, *keyword.values()])
    if is_tool:
        result = context.tools.call(node, function, arguments, context.guard)
        return context.new(result, Origin(EventKind.CALL, node, function, inputs))
    result = call_builtin(node, function, arguments, context.guard)
    return context.new(result, Origin(EventKind.OPERATION, node, function, inputs))
