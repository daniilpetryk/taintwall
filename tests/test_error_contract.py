# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The typed-error contract (Q3): exact code, line and detail, and exact limits.

The planner retries from these three fields alone, so each is pinned: the
line points at the failing statement, and the detail is plan text (a name,
an operator or a construct) or empty, never data. Limits are tested at the
boundary, one value inside and one outside.
"""

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import Interpreter, Limits
from taintwall.core.labels import Label, Labeled, Source
from taintwall.core.mint import Mint
from taintwall.core.plan import MAX_PLAN_CHARS, MAX_PLAN_DEPTH, parse_plan
from taintwall.core.values import Record

MINT = Mint()
PRELUDE = "ok = 1\n"  # every case starts on line 2, so a wrong line shows up


def record() -> Labeled[object]:
    label = Label.of(Source.tool("record"))
    return Labeled(Record.of({"a": Labeled(1, label)}), label)


def boom() -> Labeled[object]:
    raise RuntimeError


def run(source: str, limits: Limits | None = None) -> None:
    tools = {"record": record, "boom": boom}
    interpreter = Interpreter(tools, requester=MINT.owner(), mint=MINT, limits=limits)
    interpreter.run(parse_plan(source))


def failure(source: str, limits: Limits | None = None) -> PlanError:
    with pytest.raises(PlanError) as err:
        run(source, limits)
    return err.value


C = PlanErrorCode


@pytest.mark.parametrize(
    ("source", "code", "line", "detail"),
    [
        ("x = y", C.UNKNOWN_NAME, 2, "y"),
        ("x = nope()", C.UNKNOWN_TOOL, 2, "nope"),
        ("r = record()\nx = r.zz", C.MISSING_FIELD, 3, "zz"),
        ("x = (5).real", C.TYPE_MISMATCH, 2, "real"),
        ("x = 'a' - 1", C.TYPE_MISMATCH, 2, "Sub"),
        ("x = 'a' * 1.5", C.TYPE_MISMATCH, 2, "Mult"),
        ("x = -'a'", C.TYPE_MISMATCH, 2, "USub"),
        ("x = (1, 2)[5]", C.INDEX_OUT_OF_RANGE, 2, ""),
        ("x = (1, 2)['a']", C.TYPE_MISMATCH, 2, ""),
        ("x = 1 / 0", C.DIVISION_BY_ZERO, 2, ""),
        ("x = 1 % 0", C.DIVISION_BY_ZERO, 2, ""),
        ("x = len()", C.BAD_ARGUMENTS, 2, "len"),
        ("x = len(1, 2)", C.BAD_ARGUMENTS, 2, "len"),
        ("x = abs(1, 2)", C.BAD_ARGUMENTS, 2, "abs"),
        ("x = len((1,), key=1)", C.BAD_ARGUMENTS, 2, "len"),
        ("x = len(5)", C.TYPE_MISMATCH, 2, ""),
        ("r = {'a': 1}\nx = max(((r,),))", C.TYPE_MISMATCH, 3, "max"),
        ("x = {'Bad-Key': 1}", C.TYPE_MISMATCH, 2, "record"),
        ("x = f'{(1,)}'", C.TYPE_MISMATCH, 2, "format"),
        ("x = boom()", C.TOOL_FAILED, 2, "boom"),
        ("len = 1", C.UNSUPPORTED, 2, "len"),
        ("x = 'a' * 200000", C.VALUE_TOO_LARGE, 2, ""),
    ],
)
def test_runtime_errors(source: str, code: PlanErrorCode, line: int, detail: str) -> None:
    err = failure(PRELUDE + source)
    assert (err.code, err.line, err.detail) == (code, line, detail)


@pytest.mark.parametrize(
    ("source", "detail"),
    [
        ("import os", "Import"),
        ("x = (y := 1)", "NamedExpr"),
        ("x = a.__class__", "private name"),
        ("x = _p", "private name"),
        ("x = f(_p=1)", "private name"),
        ("x = f(*a)", "star argument"),
        ("x = f(**a)", "keyword unpacking"),
        ("x = text.pop()", "method call"),
        ("x = {1: 2}", "record key"),
        ("x = f'{a!r}'", "format spec"),
        ("a, b = 1, 2", "assignment target"),
        ("x = b'z'", "constant type"),
        ("x = \uff58", "non-ascii name"),
    ],
)
def test_parse_errors(source: str, detail: str) -> None:
    with pytest.raises(PlanError) as err:
        parse_plan(PRELUDE + source)
    assert (err.value.code, err.value.line, err.value.detail) == (C.UNSUPPORTED, 2, detail)


def test_error_text_format() -> None:
    assert str(PlanError(C.SYNTAX)) == "syntax"
    assert str(PlanError(C.SYNTAX, 3)) == "syntax at line 3"
    assert str(PlanError(C.UNKNOWN_NAME, 3, "y")) == "unknown_name at line 3 (y)"


class TestBoundaries:
    @pytest.mark.parametrize(
        ("source", "fits"),
        [("x = 'a' * 5", True), ("x = 'a' * 6", False), ("x = 'ab' + 'abc'", True)],
    )
    def test_string_length(self, source: str, *, fits: bool) -> None:
        self.check(source, Limits(max_string=5), fits=fits)

    @pytest.mark.parametrize(
        ("source", "fits"),
        [("a = 'abc'\nx = f'{a}ab'", True), ("a = 'abc'\nx = f'{a}{a}'", False)],
    )
    def test_format_length(self, source: str, *, fits: bool) -> None:
        self.check(source, Limits(max_string=5), fits=fits)

    @pytest.mark.parametrize(
        ("source", "fits"),
        [
            ("x = (1,) * 3", True),
            ("x = (1,) * 4", False),
            ("x = (1, 2) + (3,)", True),
            ("x = (1, 2) + (3, 4)", False),
            ("x = {'a': 1, 'b': 2, 'c': 3}", True),
            ("x = {'a': 1, 'b': 2, 'c': 3, 'd': 4}", False),
        ],
    )
    def test_items(self, source: str, *, fits: bool) -> None:
        self.check(source, Limits(max_items=3), fits=fits)

    @pytest.mark.parametrize(("source", "fits"), [("x = 255", True), ("x = 256", False)])
    def test_integer_bits(self, source: str, *, fits: bool) -> None:
        self.check(source, Limits(max_int_bits=8), fits=fits)

    def test_steps(self) -> None:
        run("x = 1 + 2", Limits(max_steps=3))
        assert failure("x = 1 + 2 + 3", Limits(max_steps=3)).code is C.STEP_LIMIT

    def test_plan_length(self) -> None:
        exact = "x = 1" + " " * (MAX_PLAN_CHARS - 5)
        parse_plan(exact)
        with pytest.raises(PlanError) as err:
            parse_plan(exact + " ")
        assert err.value.code is C.TOO_LARGE

    def test_plan_depth(self) -> None:
        # Module > Assign > k UnaryOps > Constant: the constant sits at depth k + 2.
        parse_plan("x = " + "-" * (MAX_PLAN_DEPTH - 2) + "1")
        with pytest.raises(PlanError) as err:
            parse_plan("x = " + "-" * (MAX_PLAN_DEPTH - 1) + "1")
        assert (err.value.code, err.value.line) == (C.TOO_DEEP, 1)

    @staticmethod
    def check(source: str, limits: Limits, *, fits: bool) -> None:
        if fits:
            run(source, limits)
        else:
            assert failure(source, limits).code is C.VALUE_TOO_LARGE
