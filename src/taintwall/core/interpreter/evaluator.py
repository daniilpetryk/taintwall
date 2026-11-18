# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Evaluator: walks a checked plan and dispatches each syntax node.

The evaluator holds the state of one run (variables, guard, trace) and
implements ``Context`` for the node handlers in ``reads``, ``literals`` and
``operations``. It contains no labeling rule itself, except that a variable
may not reuse a tool or builtin name, so one name has one meaning.
"""

import ast
import dataclasses
import types
from collections.abc import Callable, Mapping
from typing import Final

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter import literals, operations, reads
from taintwall.core.interpreter.builtins import BUILTINS
from taintwall.core.interpreter.context import Context
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.operators import Result
from taintwall.core.interpreter.tools import Toolbox
from taintwall.core.interpreter.trace import Origin, Ref, Trace, TraceEvent
from taintwall.core.labels import (
    Label,
    Labeled,
    MutableValueError,
    NonFiniteNumberError,
    ValueTooDeepError,
)


@dataclasses.dataclass(frozen=True, slots=True)
class RunResult:
    """Variables after the run, the provenance trace, and steps used."""

    variables: Mapping[str, Labeled[object]]
    trace: tuple[TraceEvent, ...]
    steps: int


class Evaluator:
    """State of one plan run; the ``Context`` every node handler sees."""

    def __init__(self, tools: Toolbox, literal_label: Label, guard: Guard) -> None:
        self._tools = tools
        self._literal_label = literal_label
        self._guard = guard
        self._trace = Trace()
        self._variables: dict[str, Ref] = {}

    @property
    def guard(self) -> Guard:
        """Limits of this run."""
        return self._guard

    @property
    def literal_label(self) -> Label:
        """Label of every plan literal: the plan and the requester."""
        return self._literal_label

    @property
    def tools(self) -> Toolbox:
        """Tools the plan may call."""
        return self._tools

    def result(self) -> RunResult:
        """What the run produced so far."""
        variables = {name: ref.labeled for name, ref in self._variables.items()}
        return RunResult(types.MappingProxyType(variables), self._trace.events, self._guard.steps)

    def statement(self, node: ast.stmt) -> None:
        """Run one top-level statement."""
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            assert isinstance(target, ast.Name)  # noqa: S101 - parse_plan guarantees it
            if target.id in self._tools or target.id in BUILTINS:
                raise error_at(PlanErrorCode.UNSUPPORTED, node, target.id)
            ref = self.expr(node.value)
            self._trace.assign(ref, target.id, node)
            self._variables[target.id] = ref
        elif isinstance(node, ast.Expr):
            self.expr(node.value)

    def expr(self, node: ast.expr) -> Ref:
        """Evaluate one expression."""
        self._guard.step(node)
        return _HANDLERS[type(node)](self, node)

    def variable(self, name: str) -> Ref | None:
        """The value of variable ``name``, if it is assigned."""
        return self._variables.get(name)

    def new(self, result: Result, origin: Origin) -> Ref:
        """Make ``result`` a labeled value and record it in the trace."""
        value, label = result
        self._guard.check_size(value, origin.node)
        try:
            labeled = Labeled(value, label)
        except (NonFiniteNumberError, ValueTooDeepError):
            raise error_at(PlanErrorCode.VALUE_TOO_LARGE, origin.node) from None
        except MutableValueError:
            raise error_at(PlanErrorCode.TYPE_MISMATCH, origin.node) from None
        return self._trace.add(labeled, origin)


_HANDLERS: Final[Mapping[type[ast.expr], Callable[[Context, ast.expr], Ref]]] = {
    ast.Constant: literals.constant,
    ast.Tuple: literals.sequence,
    ast.List: literals.sequence,
    ast.Dict: literals.record,
    ast.JoinedStr: literals.formatted,
    ast.Name: reads.name,
    ast.Attribute: reads.attribute,
    ast.Subscript: reads.subscript,
    ast.BinOp: operations.binary_op,
    ast.UnaryOp: operations.unary_op,
    ast.Compare: operations.comparison,
    ast.BoolOp: operations.boolean,
    ast.Call: operations.call,
}
"""One handler per syntax node the plan language allows (see ``plan.py``)."""
