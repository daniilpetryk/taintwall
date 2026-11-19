# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Internal pieces tested directly: defensive paths and exact limit boundaries.

``parse_plan`` and the size limits keep plans away from the defensive
branches, so they are exercised here through the module APIs. They must stay
fail-closed in case a later change to the plan language opens a way in.
"""

import ast
from collections.abc import Callable

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import (
    Interpreter,
    InvalidLimitError,
    InvalidRequesterError,
    Limits,
    ToolNameClashError,
)
from taintwall.core.interpreter.control import Dependence, Flow, ImplicitFlows
from taintwall.core.interpreter.evaluator import Evaluator
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.operators import apply
from taintwall.core.interpreter.statements import block
from taintwall.core.interpreter.tools import Toolbox
from taintwall.core.interpreter.trace import EventKind, Origin
from taintwall.core.labels import Label, Labeled, Source
from taintwall.core.mint import Mint
from taintwall.core.values import InvalidFieldError, NotLabeledFieldError, Record

EXPRESSION = ast.parse("\n\nx = 1 + 2").body[0].value  # type: ignore[attr-defined]
MINT = Mint()
OWNER = MINT.owner()
LABEL = Label.of(MINT.plan())


def _tool() -> Labeled[object]:
    return Labeled(1, LABEL)


def evaluator() -> Evaluator:
    return Evaluator(Toolbox({}), LABEL, Guard(Limits()), Flow(ImplicitFlows.STRICT))


def raises(error: type[BaseException]) -> object:
    raise error


def test_overflow_becomes_value_too_large_at_the_node() -> None:
    with pytest.raises(PlanError) as err:
        apply(EXPRESSION, raises, OverflowError)
    assert (err.value.code, err.value.line) == (PlanErrorCode.VALUE_TOO_LARGE, 3)


def test_a_mutable_result_is_a_type_mismatch_at_the_node() -> None:
    with pytest.raises(PlanError) as err:
        evaluator().new(({"a": 1}, LABEL), Origin(EventKind.OPERATION, EXPRESSION))
    assert (err.value.code, err.value.line) == (PlanErrorCode.TYPE_MISMATCH, 3)


def test_a_non_finite_result_is_too_large_at_the_node() -> None:
    with pytest.raises(PlanError) as err:
        evaluator().new((float("inf"), LABEL), Origin(EventKind.OPERATION, EXPRESSION))
    assert (err.value.code, err.value.line) == (PlanErrorCode.VALUE_TOO_LARGE, 3)


@pytest.mark.parametrize(("source", "detail"), [("del x", "Delete"), ("import os", "Import")])
def test_unknown_statements_fail_closed(source: str, detail: str) -> None:
    with pytest.raises(PlanError) as err:
        block(evaluator(), ast.parse("\n" + source).body)
    assert (err.value.code, err.value.line, err.value.detail) == (
        PlanErrorCode.UNSUPPORTED,
        2,
        detail,
    )


class TestGuard:
    def test_visit_budget_is_exact(self) -> None:
        guard = Guard(Limits(max_visits=2))
        guard.at(ast.parse("\n\nx = 1").body[0])
        guard.visit()
        guard.visit()
        with pytest.raises(PlanError) as err:
            guard.visit()
        assert (err.value.code, err.value.line) == (PlanErrorCode.STEP_LIMIT, 3)

    def test_step_budget_is_exact(self) -> None:
        guard = Guard(Limits(max_steps=1))
        guard.step()
        with pytest.raises(PlanError):
            guard.step()
        assert guard.steps == 2

    def test_repeat_uses_the_limit_of_its_type(self) -> None:
        guard = Guard(Limits(max_string=100, max_items=3))
        guard.check_repeat("ab", 50)  # 100 characters fit the string limit
        guard.check_repeat((1,), 3)
        with pytest.raises(PlanError):
            guard.check_repeat((1,), 4)
        with pytest.raises(PlanError):
            guard.check_repeat("ab", 51)

    def test_negative_repeat_is_empty(self) -> None:
        Guard(Limits(max_items=1)).check_repeat((1, 2, 3), -5)

    def test_smallest_limits_are_allowed(self) -> None:
        limits = Limits(max_steps=1, max_string=1, max_items=1, max_int_bits=1, max_visits=1)
        assert limits.max_visits == 1


@pytest.mark.parametrize(
    ("make", "error", "shown"),
    [
        (lambda: Limits(max_steps=0), InvalidLimitError, ["max_steps", "positive integer"]),
        (
            lambda: Interpreter({"len": _tool, "abs": _tool}, requester=OWNER, mint=MINT),
            ToolNameClashError,
            ["abs, len"],
        ),
        (
            lambda: Interpreter({}, requester=Source.tool("x"), mint=MINT),
            InvalidRequesterError,
            ["requester must be the owner or a precise principal source"],
        ),
        (
            lambda: Interpreter({}, requester=Source.tool("y"), mint=MINT),
            InvalidRequesterError,
            [],
        ),
        (lambda: Record.of({"Bad": Labeled(1, LABEL)}), InvalidFieldError, ["'Bad'"]),
        (
            lambda: Record((("a", Labeled(1, LABEL)), ("a", Labeled(2, LABEL)))),
            InvalidFieldError,
            ["'a'"],
        ),
        (lambda: Record.of({"a": 1}), NotLabeledFieldError, ["field a"]),  # type: ignore[dict-item]
    ],
)
def test_setup_errors_name_the_problem(
    make: Callable[[], object], error: type[Exception], shown: list[str]
) -> None:
    with pytest.raises(error) as err:
        make()
    assert all(text in str(err.value) for text in shown), str(err.value)
    if error is InvalidRequesterError:
        assert str(err.value) == "requester must be the owner or a precise principal source"


class TestDependence:
    def test_a_covered_condition_adds_no_id(self) -> None:
        # A while loop evaluates its test once per iteration; listing every
        # id made long loops quadratic.
        frame = Dependence.on(LABEL, 1)
        frame.extend(Dependence.on(LABEL, 2))
        assert frame.ids == (1,)

    def test_a_new_source_adds_its_id_once(self) -> None:
        frame = Dependence.on(LABEL, 1)
        other = Dependence.on(Label.of(Source.tool("t")), 2)
        frame.extend(other)
        frame.extend(other)
        assert frame.ids == (1, 2)
        assert frame.label == LABEL.join(other.label)  # type: ignore[arg-type]

    def test_merging_returns_a_new_object(self) -> None:
        frame = Dependence.on(LABEL, 1)
        assert frame.merged(Dependence()) is not frame
        assert frame.merged(Dependence()) == frame
