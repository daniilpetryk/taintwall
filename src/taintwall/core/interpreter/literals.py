# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Literals and values the plan builds: constants, tuples, records, f-strings.

A literal carries the plan and the requester (contribution C). A built tuple
or record carries the literal label, while each element keeps its own. An
f-string interpolates atoms only and joins every interpolated label.
"""

import ast

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.context import Context
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.operators import ATOMS
from taintwall.core.interpreter.trace import EventKind, Origin, Ref
from taintwall.core.interpreter.walk import check_nesting, deep_label
from taintwall.core.labels import join_all
from taintwall.core.values import Record


def constant(context: Context, node: ast.expr) -> Ref:
    """A literal such as ``5`` or ``"USDC"``."""
    assert isinstance(node, ast.Constant)  # noqa: S101 - dispatch guarantees the type
    return context.new((node.value, context.literal_label), Origin(EventKind.LITERAL, node))


def sequence(context: Context, node: ast.expr) -> Ref:
    """``(a, b)`` or ``[a, b]``, both immutable tuples."""
    assert isinstance(node, ast.Tuple | ast.List)  # noqa: S101 - dispatch guarantees the type
    refs = [context.expr(item) for item in node.elts]
    value = tuple(ref.labeled for ref in refs)
    check_nesting(value, context.guard)
    origin = Origin(EventKind.BUILD, node, "sequence", tuple(ref.id for ref in refs))
    return context.new((value, context.literal_label), origin)


def record(context: Context, node: ast.expr) -> Ref:
    """``{"field": value}``."""
    assert isinstance(node, ast.Dict)  # noqa: S101 - dispatch guarantees the type
    names = [str(key.value) for key in node.keys if isinstance(key, ast.Constant)]
    refs = [context.expr(value) for value in node.values]
    try:
        # parse_plan guarantees one key per value; strict only documents it.
        fields = dict(zip(names, (ref.labeled for ref in refs), strict=True))  # pragma: no mutate
        built = Record.of(fields)
    except (ValueError, TypeError):
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, "record") from None
    check_nesting(built, context.guard)
    origin = Origin(EventKind.BUILD, node, "record", tuple(ref.id for ref in refs))
    return context.new((built, context.literal_label), origin)


def formatted(context: Context, node: ast.expr) -> Ref:
    """An f-string; a running length stops before every part is joined."""
    assert isinstance(node, ast.JoinedStr)  # noqa: S101 - dispatch guarantees the type
    parts: list[str] = []
    refs: list[Ref] = []
    length = 0
    for part in node.values:
        if isinstance(part, ast.FormattedValue):
            refs.append(context.expr(part.value))
            if type(refs[-1].labeled.value) not in ATOMS:
                raise error_at(PlanErrorCode.TYPE_MISMATCH, part, "format")
            parts.append(str(refs[-1].labeled.value))
        else:
            assert isinstance(part, ast.Constant)  # noqa: S101 - f-string parts are one of the two
            parts.append(str(part.value))
        length += len(parts[-1])
        context.guard.check_string_length(length)
    labels = (deep_label(ref.labeled, context.guard) for ref in refs)
    label = join_all(context.literal_label, *labels)
    origin = Origin(EventKind.OPERATION, node, "format", tuple(ref.id for ref in refs))
    return context.new(("".join(parts), label), origin)
