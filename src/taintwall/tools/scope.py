# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Which reads of a variable come before anything binds it.

A run reads a variable only after a statement bound it: ``x = ...`` once its
value is computed, ``for x in ...`` once its iterable is, ``x += ...`` only
after an earlier binding, since it reads ``x`` first. A read is early when no
binding of its name ends before it in the plan text and none sits in a loop
that also holds the read (a later iteration may see it). An early read fails
whatever the data, as ``UNKNOWN_NAME``. A binding inside an ``if`` counts:
whether it ran depends on data, so a read after it may succeed.
"""

import ast
import dataclasses
from collections.abc import Iterator

type Position = tuple[int, int]
"""Line and column in the plan text."""


@dataclasses.dataclass(frozen=True, slots=True)
class _Event:
    name: str
    at: Position
    loops: frozenset[int]
    """The loops (by node id) whose every iteration includes this event."""
    node: ast.AST | None = None
    """The read itself; ``None`` for a binding."""


def early_reads(tree: ast.Module) -> frozenset[int]:
    """The ids of the ``Name`` reads and ``x += ...`` statements that read too early."""
    events = list(_events(tree, frozenset()))
    bindings = [event for event in events if event.node is None]
    return frozenset(
        id(read.node)
        for read in events
        if read.node is not None
        and not any(
            binding.name == read.name
            and (binding.at <= read.at or bool(binding.loops & read.loops))
            for binding in bindings
        )
    )


def _events(node: ast.AST, loops: frozenset[int]) -> Iterator[_Event]:
    """Reads and bindings under ``node``, each with where it happens."""
    match node:
        case ast.Name(name, ast.Load()):
            yield _Event(name, _start(node), loops, node)
        case ast.Assign([ast.Name(name)], value):
            yield from _events(value, loops)
            yield _Event(name, _end(node), loops)
        case ast.AugAssign(ast.Name(name), _, value):
            yield _Event(name, _start(node), loops, node)
            yield from _events(value, loops)
            yield _Event(name, _end(node), loops)
        case ast.For() | ast.While():
            yield from _loop(node, loops | {id(node)}, loops)
        case _:
            for child in ast.iter_child_nodes(node):
                yield from _events(child, loops)


def _loop(node: ast.AST, inside: frozenset[int], outside: frozenset[int]) -> Iterator[_Event]:
    """A loop: a ``for`` evaluates its iterable once, before; everything else repeats."""
    if isinstance(node, ast.For):
        assert isinstance(node.target, ast.Name)  # noqa: S101 - parse_plan allows no other
        yield from _events(node.iter, outside)
        yield _Event(node.target.id, _end(node.iter), inside)
        for statement in node.body:
            yield from _events(statement, inside)
    else:
        for child in ast.iter_child_nodes(node):
            yield from _events(child, inside)


def _start(node: ast.AST) -> Position:
    return (node.lineno, node.col_offset)  # type: ignore[attr-defined]


def _end(node: ast.AST) -> Position:
    return (node.end_lineno, node.end_col_offset)  # type: ignore[attr-defined]
