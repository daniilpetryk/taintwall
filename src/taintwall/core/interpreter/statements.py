# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Statements: assignment, ``if``, ``for``, ``while``, ``break``, ``continue``.

Each handler returns how its block ended (``Signal``). The pc rules live in
``control``; handlers only say which condition a block depends on:

* ``if``: its test;
* ``for``: the iterated value's own label, since the number of iterations is
  its length; each element joins its own label with that one, like ``t[i]``;
* ``while``: every test evaluated, since each decides whether to go on.

A test or an iterated value nested in further ``Labeled`` wrappers is
unwrapped, and every wrapper's label counts (``walk.truth``).

Every loop iteration costs a step, so a loop over a long tuple with an empty
body still exhausts the step budget.
"""

import ast
from collections.abc import Callable, Mapping, Sequence
from typing import Final

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.builtins import BUILTINS
from taintwall.core.interpreter.context import Scope
from taintwall.core.interpreter.control import Dependence, Signal
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.trace import EventKind, Origin, Ref
from taintwall.core.interpreter.walk import truth, unwrap
from taintwall.core.labels import Label, Labeled


def block(scope: Scope, statements: Sequence[ast.stmt]) -> Signal:
    """Run statements in order until one leaves the block."""
    for statement in statements:
        scope.guard.at(statement)
        handler = _HANDLERS.get(type(statement), _unsupported)
        signal = handler(scope, statement)
        if signal is not Signal.NEXT:
            return signal
    return Signal.NEXT


def _unsupported(scope: Scope, node: ast.stmt) -> Signal:
    """parse_plan admits no other statement; fail closed if that ever changes."""

    def fail() -> Signal:
        raise error_at(PlanErrorCode.UNSUPPORTED, node, type(node).__name__)

    return scope.attempt(fail)


def _assign(scope: Scope, node: ast.stmt) -> Signal:
    assert isinstance(node, ast.Assign)  # noqa: S101 - dispatch guarantees the type
    target = node.targets[0]
    assert isinstance(target, ast.Name)  # noqa: S101 - parse_plan guarantees it
    name = target.id

    def run() -> None:
        _check_target(scope, name, node)
        scope.bind(name, scope.expr(node.value), node)

    scope.attempt(run)
    return Signal.NEXT


def _check_target(scope: Scope, name: str, node: ast.stmt) -> None:
    """A variable may not reuse a tool or builtin name: one name, one meaning.

    Checked before the value runs, so a rejected plan calls no tool.
    """
    if name in scope.tools or name in BUILTINS:
        raise error_at(PlanErrorCode.UNSUPPORTED, node, name)


def _expression(scope: Scope, node: ast.stmt) -> Signal:
    assert isinstance(node, ast.Expr)  # noqa: S101 - dispatch guarantees the type
    scope.attempt(lambda: scope.expr(node.value))
    return Signal.NEXT


def _if(scope: Scope, node: ast.stmt) -> Signal:
    assert isinstance(node, ast.If)  # noqa: S101 - dispatch guarantees the type
    test = scope.attempt(lambda: scope.expr(node.test))
    holds, label = truth(test.labeled)
    condition = Dependence.on(label, test.id)
    with scope.flow.branch(condition):
        signal = block(scope, node.body if holds else node.orelse)
    _settle(scope, node, condition)
    if scope.flow.leaves_loop(node):
        scope.flow.exit(condition)
    return signal


def _for(scope: Scope, node: ast.stmt) -> Signal:
    assert isinstance(node, ast.For)  # noqa: S101 - dispatch guarantees the type
    target = node.target
    assert isinstance(target, ast.Name)  # noqa: S101 - parse_plan guarantees it
    name = target.id

    def start() -> tuple[Ref, tuple[object, ...], Label]:
        _check_target(scope, name, node)
        source = scope.expr(node.iter)
        items, container = unwrap(source.labeled)
        if type(items) is not tuple:
            raise error_at(PlanErrorCode.TYPE_MISMATCH, node.iter)
        return source, items, container

    source, items, container = scope.attempt(start)
    frame = Dependence.on(container, source.id)
    with scope.flow.loop(frame):
        for item in items:

            def bind(item: object = item) -> None:
                scope.guard.at(node)
                scope.guard.step()
                scope.bind(name, _element(scope, node, (source.id, container), item), node)

            scope.attempt(bind)
            if block(scope, node.body) is Signal.BREAK:
                break
    _settle(scope, node, frame)
    return Signal.NEXT


def _element(scope: Scope, node: ast.For, source: tuple[int, Label], item: object) -> Ref:
    """One element of the iterated tuple, labeled like ``t[i]``."""
    source_id, container = source
    value, own = (item.value, item.label) if isinstance(item, Labeled) else (item, container)
    origin = Origin(EventKind.INDEX, node.target, "", (source_id,))
    return scope.new((value, own.join(container)), origin)


def _while(scope: Scope, node: ast.stmt) -> Signal:
    assert isinstance(node, ast.While)  # noqa: S101 - dispatch guarantees the type
    frame = Dependence()
    with scope.flow.loop(frame):
        while True:
            scope.guard.at(node)
            test = scope.attempt(lambda: scope.expr(node.test))
            holds, label = truth(test.labeled)
            frame.extend(Dependence.on(label, test.id))
            scope.flow.grow()
            if not holds or block(scope, node.body) is Signal.BREAK:
                break
    _settle(scope, node, frame)
    return Signal.NEXT


def _settle(scope: Scope, node: ast.stmt, condition: Dependence) -> None:
    """After a block, at its line: variables it may assign depend on ``condition``."""
    scope.guard.at(node)
    scope.attempt(lambda: scope.settle(node, condition))


def _pass(_scope: Scope, _node: ast.stmt) -> Signal:
    return Signal.NEXT


def _break(_scope: Scope, _node: ast.stmt) -> Signal:
    return Signal.BREAK


def _continue(_scope: Scope, _node: ast.stmt) -> Signal:
    return Signal.CONTINUE


_HANDLERS: Final[Mapping[type[ast.stmt], Callable[[Scope, ast.stmt], Signal]]] = {
    ast.Assign: _assign,
    ast.Expr: _expression,
    ast.If: _if,
    ast.For: _for,
    ast.While: _while,
    ast.Pass: _pass,
    ast.Break: _break,
    ast.Continue: _continue,
}
"""One handler per statement the plan language allows (see ``plan.py``)."""
