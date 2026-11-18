# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Reading variables, fields and elements.

Reading a field or an element joins its own label with the container's (and
the index's) label. A record key computed from data never reaches errors or
the trace (Q3); only a field name written in the plan does.
"""

import ast

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.context import Context
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.trace import EventKind, Origin, Ref
from taintwall.core.labels import Labeled, join_all
from taintwall.core.values import Record


def name(context: Context, node: ast.expr) -> Ref:
    """A variable."""
    assert isinstance(node, ast.Name)  # noqa: S101 - dispatch guarantees the type
    ref = context.variable(node.id)
    if ref is None:
        raise error_at(PlanErrorCode.UNKNOWN_NAME, node, node.id)
    return ref


def attribute(context: Context, node: ast.expr) -> Ref:
    """``record.field``."""
    assert isinstance(node, ast.Attribute)  # noqa: S101 - dispatch guarantees the type
    return _field(context, node, context.expr(node.value), node.attr, None)


def subscript(context: Context, node: ast.expr) -> Ref:
    """``sequence[index]`` or ``record[key]``."""
    assert isinstance(node, ast.Subscript)  # noqa: S101 - dispatch guarantees the type
    container, index = context.expr(node.value), context.expr(node.slice)
    sequence, position = container.labeled.value, index.labeled.value
    if isinstance(sequence, Record):
        if type(position) is not str:
            raise error_at(PlanErrorCode.TYPE_MISMATCH, node)
        return _field(context, node, container, position, index)
    if type(sequence) not in {tuple, str} or type(position) is not int:
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node)
    try:
        element = sequence[position]  # type: ignore[index]
    except IndexError:
        raise error_at(PlanErrorCode.INDEX_OUT_OF_RANGE, node) from None
    own = element.label if isinstance(element, Labeled) else container.labeled.label
    value = element.value if isinstance(element, Labeled) else element
    label = join_all(own, container.labeled.label, index.labeled.label)
    return context.new((value, label), Origin(EventKind.INDEX, node, "", (container.id, index.id)))


def _field(
    context: Context, node: ast.expr, container: Ref, field_name: str, key: Ref | None
) -> Ref:
    """Field ``field_name``: from ``r.name`` (``key`` is None) or ``r[key]``."""
    detail = field_name if key is None else ""
    extra = () if key is None else (key,)
    record = container.labeled.value
    if not isinstance(record, Record):
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, detail)
    field = record.get(field_name)
    if field is None:
        raise error_at(PlanErrorCode.MISSING_FIELD, node, detail)
    labels = (field.label, container.labeled.label, *(ref.labeled.label for ref in extra))
    origin = Origin(EventKind.FIELD, node, detail, (container.id, *(ref.id for ref in extra)))
    return context.new((field.value, join_all(*labels)), origin)
