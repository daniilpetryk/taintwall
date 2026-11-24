# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The static plan checker: the same errors as a run, before it, and no false ones."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter import LabeledPlanError
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.tools import (
    MAX_ERRORS,
    READ_ONLY,
    Effects,
    Implementation,
    Known,
    ListOf,
    Maybe,
    OneOf,
    Param,
    RecordOf,
    Registry,
    Scalar,
    ToolSpec,
    may_fit,
)

MINT = Mint()
C = PlanErrorCode
TX = RecordOf("Transaction", (("amount", Scalar.FLOAT), ("to", Scalar.STR)))
SEND = Effects(moves_funds=True, outward=True)
SPECS: list[tuple[ToolSpec, Implementation]] = [
    (ToolSpec("balance", "B.", (), Scalar.FLOAT, READ_ONLY), lambda: 10.0),
    (
        ToolSpec("txs", "T.", (Param("n", Scalar.INT, 2),), ListOf(TX), READ_ONLY),
        lambda n=2: [{"amount": 1.5, "to": "a"}] * n,
    ),
    (
        ToolSpec(
            "send",
            "S.",
            (Param("to", Scalar.STR), Param("amount", Scalar.FLOAT)),
            Scalar.BOOL,
            SEND,
        ),
        lambda to, amount: True,  # noqa: ARG005 - the arguments are checked, not used
    ),
    (
        ToolSpec("tag", "T.", (Param("kind", OneOf(("a", "b"))),), Scalar.STR, READ_ONLY),
        lambda kind: kind,
    ),
    (
        ToolSpec("note", "N.", (Param("text", Maybe(Scalar.STR), None),), Scalar.STR, READ_ONLY),
        lambda text=None: text or "",
    ),
    (
        ToolSpec("keep", "K.", (Param("tx", TX),), Scalar.BOOL, Effects(writes=True)),
        lambda tx: True,  # noqa: ARG005 - the argument is checked, not used
    ),
]
TOOLS = Registry(SPECS)


def run_error(plan: str) -> tuple[C, int | None, str] | None:
    interpreter, _ = TOOLS.interpreter(requester=MINT.owner(), mint=MINT)
    try:
        interpreter.run(parse_plan(plan))
    except LabeledPlanError as err:
        return (err.code, err.line, err.detail)
    return None


def static_errors(plan: str) -> list[tuple[C, int | None, str]]:
    return [(e.code, e.line, e.detail) for e in TOOLS.check(parse_plan(plan)).errors]


BAD = [
    "x = foo(1)",
    "x = y",
    "x = send('a', 'ten')",
    "x = send('a')",
    "x = send('a', 1.0, memo='m')",
    "x = send('a', 1.0, to='b')",
    "x = send('a', 1.0, 3)",
    "x = send(None, 1.0)",
    "x = send(('a',), 1.0)",
    "x = tag('c')",
    "x = txs(True)",
    "t = txs()\nx = t[0].total",
    "t = txs()\nx = t[0]['total']",
    "t = txs()\nx = t.amount",
    "x = 'a'.b",
    "b = balance()\nx = b.value",
    "r = {'a': 1}\nx = r.b",
    "len = 1",
    "send = 1",
    "for balance in (1, 2):\n    pass",
    "x += 1",
    "for c in 'abc':\n    pass",
    "x = keep(1)",
    "x = send(to=1, amount=1.0)",
]


@pytest.mark.parametrize("plan", BAD)
def test_the_checker_reports_what_the_run_would_hit(plan: str) -> None:
    expected = run_error(plan)
    assert expected is not None
    assert static_errors(plan) == [expected]


GOOD = [
    "b = balance()\nif b > 1:\n    x = send('a', b)",
    "t = txs(3)\nfor x in t:\n    if x.amount > 1:\n        ok = send(x.to, x.amount)",
    "x = send('a', 1)",
    "x = send(amount=1.0, to='a')",
    "x = tag('a')",
    "x = note()\ny = note(None)\nz = note('n')",
    "t = txs()\nx = keep(t[0])",
    "x = keep({'amount': 1.0, 'to': 'a'})",
    "x = send(f'{1:.2f}', 2.0)",
    "x = send('A'.lower(), float('2'))",
    "t = txs()\nr = t[0].get('amount')\ny = t[0]['to']",
    "x = 1\nx = 'a'\ny = send(x, 1.0)",
    "x = 'a' if balance() > 1 else 'b'\ny = send(x, 1.0)",
    "n = 0\nn += 1\nx = txs(n)",
    "for i in range(2):\n    x = send(str(i), 1.0)",
    "x = sorted((1, 2))\ny = x[0]",
    "x = None\ny = note(x)",
]


@pytest.mark.parametrize("plan", GOOD)
def test_a_plan_that_runs_passes(plan: str) -> None:
    assert run_error(plan) is None
    assert static_errors(plan) == []


def test_every_mistake_at_once_in_line_order() -> None:
    plan = "x = foo()\ny = send('a')\nz = q\nw = foo()"
    assert static_errors(plan) == [
        (C.UNKNOWN_TOOL, 1, "foo"),
        (C.BAD_ARGUMENTS, 2, "send"),
        (C.UNKNOWN_NAME, 3, "q"),
        (C.UNKNOWN_TOOL, 4, "foo"),
    ]
    assert static_errors("x = foo() + foo()") == [(C.UNKNOWN_TOOL, 1, "foo")]


def test_errors_are_capped() -> None:
    plan = "\n".join(f"x{i} = foo()" for i in range(MAX_ERRORS + 5))
    errors = static_errors(plan)
    assert len(errors) == MAX_ERRORS
    assert errors[-1] == (C.UNKNOWN_TOOL, MAX_ERRORS, "foo")


def test_calls_and_their_effects_are_listed_before_anything_runs() -> None:
    check = TOOLS.check(
        parse_plan("b = balance()\nif b > 100:\n    x = send('a', 1.0)\nk = keep(txs()[0])")
    )
    assert check.ok
    assert [(call.name, call.line) for call in check.calls] == [
        ("balance", 1),
        ("send", 3),
        ("keep", 4),
        ("txs", 4),
    ]
    assert check.effects == Effects(writes=True, moves_funds=True, outward=True)
    assert TOOLS.check(parse_plan("b = balance()")).effects == READ_ONLY


def test_inference_that_does_not_settle_assumes_nothing() -> None:
    chain = "\n".join(f"x{i + 1} = x{i}" for i in range(70))
    late = f"{chain}\nx0 = 'ten'\ny = send('a', x70)"
    assert static_errors(late) == []
    short = "\n".join(f"x{i + 1} = x{i}" for i in range(3))
    assert static_errors(f"{short}\nx0 = 'ten'\ny = send('a', x3)") == [
        (C.BAD_ARGUMENTS, 5, "send")
    ]


@pytest.mark.parametrize(
    ("value", "expected", "fits"),
    [
        (Scalar.ANY, Scalar.STR, True),
        (Scalar.STR, Scalar.ANY, True),
        (Known(1), Scalar.FLOAT, True),
        (Known("1"), Scalar.FLOAT, False),
        (Maybe(Scalar.STR), Scalar.STR, True),
        (Maybe(Scalar.INT), Scalar.STR, False),
        (Maybe(Scalar.INT), Maybe(Scalar.STR), True),
        (Scalar.NONE, Maybe(Scalar.STR), True),
        (Scalar.NONE, Scalar.STR, False),
        (Scalar.INT, Maybe(Scalar.FLOAT), True),
        (OneOf(("a", 1)), Scalar.INT, True),
        (OneOf(("a",)), Scalar.INT, False),
        (Scalar.STR, OneOf(("a",)), True),
        (Scalar.INT, OneOf(("a",)), False),
        (ListOf(Scalar.STR), OneOf(("a",)), False),
        (Scalar.INT, Scalar.FLOAT, True),
        (Scalar.FLOAT, Scalar.INT, False),
        (Scalar.BOOL, Scalar.INT, False),
        (ListOf(Scalar.INT), ListOf(Scalar.FLOAT), True),
        (ListOf(Scalar.STR), ListOf(Scalar.INT), False),
        (ListOf(Scalar.STR), Scalar.STR, False),
        (TX, RecordOf("Other", ()), True),
        (TX, ListOf(Scalar.ANY), False),
        (Scalar.STR, TX, False),
    ],
)
def test_may_fit_is_false_only_when_no_value_could(
    value: object, expected: object, *, fits: bool
) -> None:
    assert may_fit(value, expected) is fits  # type: ignore[arg-type]


EXPRESSIONS = st.sampled_from(
    [
        "1", "2.5", "'a'", "'b'", "None", "True", "(1, 2)", "('a',)", "a", "b",
        "balance()", "txs()", "txs(1)", "a.amount", "a[0]", "b[0].to",
        "send('a', 1.0)", "send(a, b)", "tag('a')", "tag(a)", "note(a)", "len(a)",
        "str(a)", "a if True else b",
    ]
)  # fmt: skip


@st.composite
def plans(draw: st.DrawFn) -> str:
    lines = []
    for _ in range(draw(st.integers(1, 5))):
        name = draw(st.sampled_from(["a", "b"]))
        value = draw(EXPRESSIONS)
        shape = draw(
            st.sampled_from(["{n} = {v}", "if a:\n    {n} = {v}", "for {n} in {v}:\n    pass"])
        )
        lines.append(shape.format(n=name, v=value))
    return "\n".join(lines)


@given(plans())
def test_the_checker_never_rejects_a_plan_that_runs(plan: str) -> None:
    if run_error(plan) is None:
        assert static_errors(plan) == []
