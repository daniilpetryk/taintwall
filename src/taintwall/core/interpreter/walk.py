# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Reading nested plan values: plain values, deep labels, truthiness, depth.

Values nest as tuples and records of ``Labeled`` values. Every walk over that
structure draws from the run's read budget, because repetition shares
references and a small plan can build a value with very many paths.
"""

import ast

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.labels import MAX_VALUE_DEPTH, Label, Labeled, join_all
from taintwall.core.values import Record


def children(value: object) -> tuple[object, ...]:
    """Values directly inside a plan value."""
    if isinstance(value, Labeled):
        return (value.value,)
    if type(value) is tuple:
        return value
    if isinstance(value, Record):
        return tuple(field for _, field in value.fields)
    return ()


def truthy(value: object) -> bool:
    """Python truthiness of a plan value, reading only its top level."""
    if type(value) is tuple:
        return len(value) > 0
    if isinstance(value, Record):
        return len(value.fields) > 0
    return bool(value)


def plain(value: object, guard: Guard, node: ast.AST) -> object:
    """The value with every nested label removed, for computing and comparing."""
    guard.visit(node)
    if isinstance(value, Labeled):
        return plain(value.value, guard, node)
    if type(value) is tuple:
        return tuple(plain(item, guard, node) for item in value)
    if isinstance(value, Record):
        return {name: plain(field, guard, node) for name, field in value.fields}
    return value


def deep_label(labeled: Labeled[object], guard: Guard, node: ast.AST) -> Label:
    """The label of a value joined with the label of everything nested in it."""
    labels = [labeled.label]
    pending: list[object] = [labeled.value]
    while pending:
        guard.visit(node)
        item = pending.pop()
        if isinstance(item, Labeled):
            labels.append(item.label)
        pending.extend(children(item))
    return join_all(*labels)


def check_nesting(value: object, guard: Guard, node: ast.AST) -> None:
    """Reject a container nested deeper than ``MAX_VALUE_DEPTH``.

    ``Labeled`` checks depth only down to the next nested ``Labeled`` value,
    so ``a = (a,)`` repeated would grow without bound and later overflow the
    stack in comparisons. Only building tuples and records adds a level, so
    the evaluator calls this there. Shared elements are measured once.
    """
    depths: dict[int, int] = {}
    pending: list[tuple[object, bool]] = [(value, False)]
    while pending:
        item, ready = pending.pop()
        inner = children(item)
        if ready or not inner:
            depths[id(item)] = _depth(item, inner, depths, node)
        elif id(item) not in depths:
            guard.visit(node)
            pending.append((item, True))
            pending.extend((child, False) for child in inner if id(child) not in depths)


def _depth(item: object, inner: tuple[object, ...], depths: dict[int, int], node: ast.AST) -> int:
    level = int(type(item) is tuple or isinstance(item, Record))
    depth = level + max((depths[id(child)] for child in inner), default=0)
    if depth > MAX_VALUE_DEPTH:
        raise error_at(PlanErrorCode.VALUE_TOO_LARGE, node)
    return depth
