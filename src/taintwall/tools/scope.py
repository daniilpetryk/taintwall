# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Which reads of a variable come before anything binds it.

A run reads a variable only after a statement bound it: ``x = ...`` once its
value is computed, ``for x in ...`` once its iterable is, ``x += ...`` only
after an earlier binding, since it reads ``x`` first. A read is early when no
binding of its name ends before it in the plan text, and no binding in a loop
around it can have run first: one in an earlier iteration counts only if the
read is conditional within that loop (under an ``if``, a conditional
expression, a later operand of ``and``/``or`` or an inner loop), since an
unconditional read runs in the first iteration before any later binding. A
``while`` test runs before its body, so the body's bindings never precede its
first evaluation. An early read fails whatever the data, as ``UNKNOWN_NAME``.
A binding inside an ``if`` counts: whether it ran depends on data, so a read
after it may succeed.
"""

import ast
import dataclasses
from collections.abc import Iterator, Sequence

type Position = tuple[int, int]
"""Line and column in the plan text."""


@dataclasses.dataclass(frozen=True, slots=True)
class _Event:
    name: str
    at: Position
    loops: frozenset[int]
    """Bindings: the loops (by node id) around them. Reads: the loops within
    which they are conditional, so a binding there may have run first."""
    node: ast.AST | None = None
    """The read itself; ``None`` for a binding."""


@dataclasses.dataclass(frozen=True, slots=True)
class _Where:
    loops: frozenset[int] = frozenset()
    """Every loop around this point."""
    guarded: frozenset[int] = frozenset()
    """The loops within which this point is conditional."""

    def branch(self) -> "_Where":
        """Under a condition: conditional within every loop around it."""
        return _Where(self.loops, self.loops)


def early_reads(tree: ast.Module) -> frozenset[int]:
    """The ids of the ``Name`` reads and ``x += ...`` statements that read too early."""
    events = list(_events(tree, _Where()))
    bindings = [event for event in events if event.node is None]
    return frozenset(
        id(read.node)
        for read in events
        if read.node is not None and not any(_precedes(binding, read) for binding in bindings)
    )


def _precedes(binding: _Event, read: _Event) -> bool:
    """Whether ``binding`` may have run before ``read``."""
    if binding.name != read.name:
        return False
    # A binding's end never coincides with a read's start.
    earlier = binding.at < read.at  # pragma: no mutate - see above
    return earlier or bool(binding.loops & read.loops)


def _events(node: ast.AST, where: _Where) -> Iterator[_Event]:
    """Reads and bindings under ``node``, each with where it happens."""
    match node:
        case ast.Name(name, ast.Load()):
            yield _Event(name, _start(node), where.guarded, node)
        case ast.Assign([ast.Name(name)], value):
            yield from _events(value, where)
            yield _Event(name, _end(node), where.loops)
        case ast.AugAssign(ast.Name(name), _, value):
            # It binds a name that must already be bound: a read, then the value.
            yield _Event(name, _start(node), where.guarded, node)
            yield from _events(value, where)
        case ast.For() | ast.While():
            yield from _loop(node, where)
        case ast.If() | ast.IfExp() | ast.BoolOp():
            yield from _branches(node, where)
        case _:
            for child in ast.iter_child_nodes(node):
                yield from _events(child, where)


def _loop(node: ast.AST, where: _Where) -> Iterator[_Event]:
    """A ``for`` evaluates its iterable once, before; a ``while`` its test first."""
    loops = where.loops | {id(node)}
    if isinstance(node, ast.For):
        assert isinstance(node.target, ast.Name)  # noqa: S101 - parse_plan allows no other
        yield from _events(node.iter, where)
        yield _Event(node.target.id, _end(node.iter), loops)
    else:
        assert isinstance(node, ast.While)  # noqa: S101 - the caller dispatches no other
        # The first test runs before the body: its bindings never precede it.
        yield from _events(node.test, where)
    # The body may run zero times, so it is conditional for the loops around
    # this one; not for this one, whose first iteration runs it in order.
    body = _Where(loops, where.loops)
    for statement in node.body:
        yield from _events(statement, body)


def _branches(node: ast.AST, where: _Where) -> Iterator[_Event]:
    """A test or first operand runs; what follows it may not."""
    first, rest = _split(node)
    yield from _events(first, where)
    for child in rest:
        yield from _events(child, where.branch())


def _split(node: ast.AST) -> tuple[ast.AST, Sequence[ast.AST]]:
    """What always runs of an ``if``, a conditional expression or ``and``/``or``, and the rest."""
    if isinstance(node, ast.If):
        return node.test, (*node.body, *node.orelse)
    if isinstance(node, ast.IfExp):
        return node.test, (node.body, node.orelse)
    assert isinstance(node, ast.BoolOp)  # noqa: S101 - the caller dispatches no other
    return node.values[0], tuple(node.values[1:])


def _start(node: ast.AST) -> Position:
    return (node.lineno, node.col_offset)  # type: ignore[attr-defined]


def _end(node: ast.AST) -> Position:
    return (node.end_lineno, node.end_col_offset)  # type: ignore[attr-defined]
