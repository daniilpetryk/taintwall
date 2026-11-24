# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The static plan checker: what a plan would get wrong, before it runs.

``parse_plan`` already refuses anything outside the language. This module
reads a parsed plan against the tool specs and finds the mistakes that would
stop it at run time whatever the data: a tool that does not exist, a
variable read before anything binds it, arguments that do not fit a tool's
parameters, a field a record type does not have, an index or a loop a value
cannot take, a tool's name reused as a variable. Every error is a typed
``PlanError`` with the code, line and detail the run would give, built from
the plan text and the specs only, never from data, so the planner may see
all of them and retry (contribution D, Q3).

The guarantee: every statement the checker reports fails whenever it runs.
Types are inferred flow-insensitively (``infer``) and a check fails only
when no value could pass, so a plan the checker accepts can still fail on
data. A statement in a branch that does not run is still reported (it is
wrong code the planner should fix), so a plan with a mistake in such a
branch can run and still be refused; a plan without branches, loops,
conditional expressions or ``and``/``or`` that runs is never refused.

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
from taintwall.tools.infer import Known, Static, infer, is_text, may_fit, typeof
from taintwall.tools.schema import ListOf, RecordOf, Scalar
from taintwall.tools.scope import early_reads
from taintwall.tools.signatures import BUILTINS as BUILTIN_RULES
from taintwall.tools.signatures import METHODS
from taintwall.tools.spec import Effects, ToolSpec

MAX_ERRORS: Final = 20
"""Most errors reported for one plan: enough to fix it, bounded for the retry prompt."""


@dataclasses.dataclass(frozen=True, slots=True)
class PlannedCall:
    """A tool call site in the plan, with what the tool may change."""

    name: str
    line: int
    effects: Effects
    constants: tuple[tuple[str, object], ...] = ()
    """Arguments the plan writes as constants, by parameter name: what an
    approval can show before anything runs (a recipient, an amount)."""


@dataclasses.dataclass(frozen=True, slots=True)
class PlanCheck:
    """The checker's verdict on a plan: its errors and its tool calls."""

    errors: tuple[PlanError, ...]
    """Ordered by line, at most ``MAX_ERRORS``; empty if the plan may run."""

    calls: tuple[PlannedCall, ...]
    """Every tool call site, in the order they would run (arguments before the
    call), whether or not they will run."""

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
    checker = _Checker(specs, infer(plan.tree, specs), early_reads(plan.tree))
    checker.visit(plan.tree)
    # One error per (line, code, detail), in that order: a name misspelled
    # twice on a line is one fix.
    found = sorted(set(checker.errors))[:MAX_ERRORS]
    errors = tuple(PlanError(PlanErrorCode(code), line, detail) for line, code, detail in found)
    return PlanCheck(errors, tuple(checker.calls))


class _Checker(ast.NodeVisitor):
    """Visits a node's parts before the node.

    A part that surely fails makes the whole expression unknown, so one
    mistake is reported once, as the run stops at it.
    """

    def __init__(
        self, specs: Mapping[str, ToolSpec], types: Mapping[str, Static], early: frozenset[int]
    ) -> None:
        self.specs = specs
        self.types = types
        self.early = early
        """Reads (by node id) that come before any binding of their name."""
        self.failed: set[int] = set()
        """Nodes (by id) found to fail."""
        self.errors: list[tuple[int, str, str]] = []
        """Line, code and detail of each failure found."""
        self.calls: list[PlannedCall] = []

    def _fail(self, code: PlanErrorCode, node: ast.expr | ast.stmt, detail: str = "") -> None:
        self.failed.add(id(node))
        self.errors.append((node.lineno, code.value, detail))

    def _typeof(self, node: ast.expr) -> Static:
        """What is known of ``node``; nothing if a part of it surely fails."""
        if any(id(child) in self.failed for child in ast.walk(node)):
            return Scalar.ANY
        return typeof(node, self.types, self.specs)

    @override
    def visit_Name(self, node: ast.Name) -> None:
        if id(node) in self.early:
            self._fail(PlanErrorCode.UNKNOWN_NAME, node, node.id)
        elif isinstance(node.ctx, ast.Store) and (node.id in self.specs or node.id in BUILTINS):
            self._fail(PlanErrorCode.UNSUPPORTED, node, node.id)

    @override
    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # ``x += y`` reads ``x`` before it binds it; a tool's name as ``x`` is
        # refused first, as the run does.
        assert isinstance(node.target, ast.Name)  # noqa: S101 - parse_plan allows no other
        name = node.target.id
        if id(node) in self.early and name not in self.specs and name not in BUILTINS:
            self._fail(PlanErrorCode.UNKNOWN_NAME, node, name)
        self.generic_visit(node)

    @override
    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        # A loop runs over a plan tuple only; a string, number or record never is one.
        if not may_fit(self._typeof(node.iter), ListOf(Scalar.ANY)):
            self._fail(PlanErrorCode.TYPE_MISMATCH, node.iter)
        self.visit(node.target)
        for statement in node.body:
            self.visit(statement)

    @override
    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            self.visit(node.func.value)
        for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
            self.visit(argument)
        if isinstance(node.func, ast.Attribute):
            self._method(node, node.func)
            return
        assert isinstance(node.func, ast.Name)  # noqa: S101 - parse_plan allows no other
        name = node.func.id
        spec = self.specs.get(name)
        if spec is not None:
            self.calls.append(
                PlannedCall(name, node.lineno, spec.effects, self._constants(spec, node))
            )
            if not self._fits(spec, node):
                self._fail(PlanErrorCode.BAD_ARGUMENTS, node, name)
        elif name not in BUILTINS:
            self._fail(PlanErrorCode.UNKNOWN_TOOL, node, name)
        else:
            self._builtin(node, name)

    def _builtin(self, node: ast.Call, name: str) -> None:
        """A builtin: positional arguments only, as many and of the kinds it takes."""
        if node.keywords:
            self._fail(PlanErrorCode.BAD_ARGUMENTS, node, name)
            return
        code = BUILTIN_RULES[name].check([self._typeof(arg) for arg in node.args])
        if code is not None:
            self._fail(code, node, name)

    def _method(self, node: ast.Call, method: ast.Attribute) -> None:
        """``get`` needs a record; every other method a string."""
        receiver = self._typeof(method.value)
        if method.attr == "get":
            never = isinstance(receiver, ListOf | Known) or receiver in _NOT_RECORDS
        else:
            never = not may_fit(receiver, Scalar.STR)
        if never:
            self._fail(PlanErrorCode.TYPE_MISMATCH, node, method.attr)
            return
        code = METHODS[method.attr].check([self._typeof(arg) for arg in node.args])
        if code is not None:
            self._fail(code, node, method.attr)

    def _constants(self, spec: ToolSpec, node: ast.Call) -> tuple[tuple[str, object], ...]:
        """The arguments written as constants, by parameter name (for an approval)."""
        names = [param.name for param in spec.params]
        given = [*zip(names, node.args, strict=False)]  # pragma: no mutate - extra ones ignored
        given += [(keyword.arg, keyword.value) for keyword in node.keywords if keyword.arg]
        known = ((name, self._typeof(value)) for name, value in given)
        return tuple((name, kind.value) for name, kind in known if isinstance(kind, Known))

    def _fits(self, spec: ToolSpec, node: ast.Call) -> bool:
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
            may_fit(self._typeof(given[param.name]), param.type)
            if param.name in given
            else not param.required
            for param in spec.params
        )

    @override
    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.visit(node.value)
        self._field(node, self._typeof(node.value), node.attr, node.attr)

    @override
    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.generic_visit(node)
        container, index = self._typeof(node.value), self._typeof(node.slice)
        if isinstance(container, RecordOf):
            if not may_fit(index, Scalar.STR):
                self._fail(PlanErrorCode.TYPE_MISMATCH, node)
            elif is_text(node.slice):
                self._field(node, container, node.slice.value, "")  # type: ignore[attr-defined]
        elif _is_sequence(container):
            # A tuple or a string takes a whole number, nothing else.
            if not may_fit(index, Scalar.INT):
                self._fail(PlanErrorCode.TYPE_MISMATCH, node)
        elif isinstance(container, Known) or container in _NOT_RECORDS:
            self._fail(PlanErrorCode.TYPE_MISMATCH, node)

    @override
    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        self.generic_visit(node)
        # An f-string formats atoms only, and a number format numbers only.
        value = self._typeof(node.value)
        atom = not isinstance(value, ListOf | RecordOf)
        number = node.format_spec is None or may_fit(value, Scalar.FLOAT)
        if not (atom and number):
            self._fail(PlanErrorCode.TYPE_MISMATCH, node, "format")

    def _field(self, node: ast.expr, record: Static, field: str, detail: str) -> None:
        """A field read: refused if the record type surely lacks it, or it is no record."""
        if isinstance(record, RecordOf) and record.fields and record.closed:
            if field not in dict(record.fields):
                self._fail(PlanErrorCode.MISSING_FIELD, node, detail)
        elif isinstance(record, ListOf | Known) or record in _NOT_RECORDS:
            self._fail(PlanErrorCode.TYPE_MISMATCH, node, detail)


_NOT_RECORDS: Final = frozenset({Scalar.STR, Scalar.INT, Scalar.FLOAT, Scalar.BOOL, Scalar.NONE})
"""Types that are never a record: reading a field of one always fails."""


def _is_sequence(kind: Static) -> bool:
    """Whether a value is surely a plan tuple or a string."""
    return (
        isinstance(kind, ListOf)
        or kind is Scalar.STR
        or (isinstance(kind, Known) and type(kind.value) is str)
    )
