# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Evaluator: walks a checked plan and dispatches each syntax node.

The evaluator holds the state of one run (variables, guard, pc, trace) and
implements ``Scope`` for the node handlers in ``reads``, ``literals``,
``operations`` and ``statements``. Its own rules: a failure is labeled by
every value its statement touched, and under strict implicit flows, values
and assignments carry the pc (see ``control``).
"""

import ast
import dataclasses
import types
from collections.abc import Callable, Mapping
from typing import Final

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import literals, operations, reads
from taintwall.core.interpreter.context import Context
from taintwall.core.interpreter.control import Dependence, Flow, LabeledPlanError
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.tools import Toolbox
from taintwall.core.interpreter.trace import EventKind, Origin, Ref, Result, Trace, TraceEvent
from taintwall.core.interpreter.walk import labels_within
from taintwall.core.labels import (
    Label,
    Labeled,
    MutableValueError,
    NonFiniteNumberError,
    ValueTooDeepError,
    join_all,
)


@dataclasses.dataclass(frozen=True, slots=True)
class RunResult:
    """Variables after the run, the provenance trace, and steps used."""

    variables: Mapping[str, Labeled[object]]
    trace: tuple[TraceEvent, ...]
    steps: int
    control: Label | None = None
    """Every condition the run depended on (``None`` for none).

    ``steps`` depends on these and on every value evaluated, so it is
    metering for the runtime, not data to show the planner.
    """


class Evaluator:
    """State of one plan run; the ``Scope`` every node handler sees."""

    def __init__(self, tools: Toolbox, literal_label: Label, guard: Guard, flow: Flow) -> None:
        self._tools = tools
        self._literal_label = literal_label
        self._guard = guard
        self._flow = flow
        self._trace = Trace()
        self._variables: dict[str, Ref] = {}
        # Reset by ``attempt`` before any statement touches a value.
        self._touched: list[Labeled[object]] = []  # pragma: no mutate

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

    @property
    def flow(self) -> Flow:
        """The pc and the implicit-flow mode of this run."""
        return self._flow

    def result(self) -> RunResult:
        """What the run produced so far."""
        variables = {name: ref.labeled for name, ref in self._variables.items()}
        events, steps = self._trace.events, self._guard.steps
        return RunResult(types.MappingProxyType(variables), events, steps, self._flow.history)

    def expr(self, node: ast.expr) -> Ref:
        """Evaluate one expression."""
        self._guard.step()
        return _HANDLERS[type(node)](self, node)

    def variable(self, name: str) -> Ref | None:
        """The value of variable ``name``, if it is assigned."""
        ref = self._variables.get(name)
        if ref is not None:
            self._touched.append(ref.labeled)
        else:
            absent = self._flow.absent(name)
            if absent is not None:
                self._touched.append(Labeled(None, absent))
        return ref

    def new(self, result: Result, origin: Origin) -> Ref:
        """Make ``result`` a labeled value and record it in the trace."""
        value, label = result
        label = self._flow.value_label(label)
        # A failure here depends on the value being made, so it carries its label.
        try:
            self._guard.check_size(value)
            labeled = Labeled(value, label)
        except PlanError as err:
            raise LabeledPlanError(err, label) from None
        except (NonFiniteNumberError, ValueTooDeepError):
            raise LabeledPlanError(
                error_at(PlanErrorCode.VALUE_TOO_LARGE, origin.node), label
            ) from None
        except MutableValueError:
            raise LabeledPlanError(
                error_at(PlanErrorCode.TYPE_MISMATCH, origin.node), label
            ) from None
        self._touched.append(labeled)
        return self._trace.add(labeled, origin, self._flow.pc.label)

    def attempt[R](self, action: Callable[[], R]) -> R:
        """Run ``action``; label a failure by everything it touched and the pc.

        A failure that already carries a label (a tool's own source, a value
        too large to make) keeps it. Under strict flows, a failure on the
        run's shared step budget also carries every value and condition the
        run has seen: how many steps were spent before depends on all of them
        (``a and b`` evaluates ``b`` only for some ``a``).
        """
        self._touched = []
        try:
            return action()
        except PlanError as err:
            failure = err
        # Raised outside the handler, so no exception chain rides along.
        labels = [self._literal_label, *labels_within(self._touched)]
        history = self._flow.history
        if self._flow.strict and failure.code is PlanErrorCode.STEP_LIMIT:
            labels.extend(self._trace.labels())
            if history is not None:
                labels.append(history)
        raise LabeledPlanError.of(failure, self._flow.value_label(join_all(*labels)))

    def bind(self, name: str, ref: Ref, node: ast.stmt) -> None:
        """Assign ``ref`` to ``name``; under strict flows it carries the pc."""
        ref = self._depend(ref, self._flow.pc, node, name)
        self._trace.assign(ref, name, node, self._flow.pc.label)
        self._variables[name] = ref

    def settle(self, node: ast.stmt, condition: Dependence) -> None:
        """Under strict flows, every variable ``node`` may assign depends on ``condition``.

        This covers branches not taken: after ``x = 0; if s: x = 1``, the
        value of ``x`` reveals ``s`` either way. A variable still unassigned
        records the condition for the failure its read would cause.
        """
        if not self._flow.strict or condition.label is None:
            return
        for name in self._flow.assigned(node):
            self._guard.visit()
            ref = self._variables.get(name)
            if ref is None:
                self._flow.skipped(name, condition.label)
            else:
                self._variables[name] = self._depend(ref, condition, node, name)

    def _depend(self, ref: Ref, condition: Dependence, node: ast.stmt, name: str) -> Ref:
        """``ref`` relabeled to depend on ``condition``, if strict and needed."""
        label = condition.label
        if not self._flow.strict or label is None or ref.labeled.label.covers(label):
            return ref
        origin = Origin(EventKind.CONTROL, node, name, (ref.id, *condition.ids))
        return self.new((ref.labeled.value, ref.labeled.label.join(label)), origin)


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
    ast.IfExp: operations.conditional,
    ast.Call: operations.call,
}
"""One handler per syntax node the plan language allows (see ``plan.py``)."""
