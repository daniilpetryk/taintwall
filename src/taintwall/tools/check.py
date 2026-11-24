# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The static plan checker: what a plan would get wrong, before it runs.

``parse_plan`` already refuses anything outside the language. This module
reads a parsed plan against the tool specs and finds the mistakes that would
stop it at run time whatever the data: a tool or variable that does not
exist, arguments that do not fit a tool's parameters, a field a record type
does not have, a tool's name reused as a variable. Every error is a typed
``PlanError`` built from the plan text and the specs only, never from data,
so the planner may see all of them and retry (contribution D, Q3).

A mistake is reported only when it would fail every time it runs: types are
inferred flow-insensitively (a variable has the join of everything assigned
to it, and anything unclear is ``object``), so a plan the checker accepts
can still fail on data, but a plan it rejects has a statement that cannot
succeed. A call in a branch that never runs is still reported: it is wrong
code, and the planner should fix it.

The checker also lists every tool call site with the tool's effects: the
summary a plan-level approval (Q10) shows before anything runs.
"""

import ast
import dataclasses
from collections.abc import Mapping
from typing import Final, override

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import BUILTINS
from taintwall.core.plan import Plan
from taintwall.tools.infer import Known, Static, assignments, infer, is_text, may_fit, typeof
from taintwall.tools.schema import ListOf, RecordOf, Scalar
from taintwall.tools.spec import Effects, ToolSpec

MAX_ERRORS: Final = 20
"""Most errors reported for one plan: enough to fix it, bounded for the retry prompt."""


@dataclasses.dataclass(frozen=True, slots=True)
class PlannedCall:
    """A tool call site in the plan, with what the tool may change."""

    name: str
    line: int
    effects: Effects


@dataclasses.dataclass(frozen=True, slots=True)
class PlanCheck:
    """The checker's verdict on a plan: its errors and its tool calls."""

    errors: tuple[PlanError, ...]
    """Ordered by line, at most ``MAX_ERRORS``; empty if the plan may run."""

    calls: tuple[PlannedCall, ...]
    """Every tool call site, in source order, whether or not it will run."""

    @property
    def ok(self) -> bool:
        """Whether no statement of the plan is sure to fail."""
        return not self.errors

    @property
    def effects(self) -> Effects:
        """Everything the plan's calls may change, together."""
        return Effects(
            writes=any(call.effects.writes for call in self.calls),
            moves_funds=any(call.effects.moves_funds for call in self.calls),
            outward=any(call.effects.outward for call in self.calls),
        )


def check_plan(plan: Plan, specs: Mapping[str, ToolSpec]) -> PlanCheck:
    """Check ``plan`` against the tools in ``specs`` without running it."""
    defined = {binding.name for binding in assignments(plan.tree) if binding.value is not None}
    checker = _Checker(specs, infer(plan.tree, specs), defined)
    checker.visit(plan.tree)
    # One error per (line, code, detail), in that order: a name misspelled
    # twice on a line is one fix.
    found = sorted(set(checker.errors))[:MAX_ERRORS]
    errors = tuple(PlanError(PlanErrorCode(code), line, detail) for line, code, detail in found)
    return PlanCheck(errors, tuple(checker.calls))


class _Checker(ast.NodeVisitor):
    def __init__(
        self, specs: Mapping[str, ToolSpec], types: Mapping[str, Static], defined: set[str]
    ) -> None:
        self.specs = specs
        self.types = types
        self.defined = defined
        """Names some ``=`` or ``for`` binds; ``x += 1`` alone reads ``x`` first."""
        self.errors: list[tuple[int, str, str]] = []
        """Line, code and detail of each failure found."""
        self.calls: list[PlannedCall] = []

    def fail(self, code: PlanErrorCode, node: ast.expr | ast.stmt, detail: str = "") -> None:
        self.errors.append((node.lineno, code.value, detail))

    def typeof(self, node: ast.expr) -> Static:
        return typeof(node, self.types, self.specs)

    @override
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id not in self.defined:
            self.fail(PlanErrorCode.UNKNOWN_NAME, node, node.id)
        elif isinstance(node.ctx, ast.Store) and (node.id in self.specs or node.id in BUILTINS):
            self.fail(PlanErrorCode.UNSUPPORTED, node, node.id)

    @override
    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # ``x += y`` reads ``x`` before it binds it.
        assert isinstance(node.target, ast.Name)  # noqa: S101 - parse_plan allows no other
        if node.target.id not in self.defined:
            self.fail(PlanErrorCode.UNKNOWN_NAME, node, node.target.id)
        self.generic_visit(node)

    @override
    def visit_For(self, node: ast.For) -> None:
        # A loop runs over a plan tuple only; a string, number or record never is one.
        if not may_fit(self.typeof(node.iter), ListOf(Scalar.ANY)):
            self.fail(PlanErrorCode.TYPE_MISMATCH, node.iter)
        self.generic_visit(node)

    @override
    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            name = node.func.id
            spec = self.specs.get(name)
            if spec is not None:
                self.calls.append(PlannedCall(name, node.lineno, spec.effects))
                if not self.fits(spec, node):
                    self.fail(PlanErrorCode.BAD_ARGUMENTS, node, name)
            elif name not in BUILTINS:
                self.fail(PlanErrorCode.UNKNOWN_TOOL, node, name)
        else:
            # A method call: check its receiver, not the method as a field.
            assert isinstance(node.func, ast.Attribute)  # noqa: S101 - parse_plan allows no other
            self.visit(node.func.value)
        for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
            self.visit(argument)

    def fits(self, spec: ToolSpec, node: ast.Call) -> bool:
        """Whether the call's arguments could bind to the spec's parameters."""
        if len(node.args) > len(spec.params):
            return False
        names = (param.name for param in spec.params)
        # Never more arguments than names (checked above); strict would only fail on fewer.
        given = dict(zip(names, node.args, strict=False))  # pragma: no mutate - see above
        for keyword in node.keywords:
            if keyword.arg is None or keyword.arg in given:
                return False
            given[keyword.arg] = keyword.value
        params = {param.name: param for param in spec.params}
        if not set(given) <= set(params):
            return False
        return all(
            may_fit(self.typeof(given[param.name]), param.type)
            if param.name in given
            else not param.required
            for param in spec.params
        )

    @override
    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.field(node, self.typeof(node.value), node.attr, node.attr)
        self.visit(node.value)

    @override
    def visit_Subscript(self, node: ast.Subscript) -> None:
        container = self.typeof(node.value)
        if isinstance(container, RecordOf) and is_text(node.slice):
            self.field(node, container, node.slice.value, "")  # type: ignore[attr-defined]
        self.generic_visit(node)

    def field(self, node: ast.expr, record: Static, field: str, detail: str) -> None:
        """A field read: refused if the record type surely lacks it, or it is no record."""
        if isinstance(record, RecordOf) and record.fields and record.closed:
            if field not in dict(record.fields):
                self.fail(PlanErrorCode.MISSING_FIELD, node, detail)
        elif isinstance(record, ListOf | Known) or record in _NOT_RECORDS:
            self.fail(PlanErrorCode.TYPE_MISMATCH, node, detail)


_NOT_RECORDS: Final = frozenset({Scalar.STR, Scalar.INT, Scalar.FLOAT, Scalar.BOOL, Scalar.NONE})
"""Types that are never a record: reading a field of one always fails."""
