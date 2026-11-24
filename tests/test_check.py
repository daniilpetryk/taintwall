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
    "y = send(x, 1.0)\nx = 1",
    "y = x.amount\nx = 'a'",
    "for a in f'{a}':\n    pass",
    "a = a + 1",
    "send += 1",
    "t = txs()\ny = t['to']",
    "b = balance()\ny = b[0]",
    "y = 'ab'['x']",
    "r = {'a': 1}\ny = r[0]",
    "y = (1, 2)[1.5]",
    "y = None[0]",
    "t = txs()\ny = t.lower()",
    "b = balance()\ny = b.get('a')",
    "t = txs()\ny = f'{t}'",
    "y = f'{True:.2f}'",
    "t = txs()\nfor c in t.lower():\n    pass",
    "t = txs()\nfor c in f'{t[0]}':\n    pass",
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
    "for i in range(2):\n    if i > 0:\n        y = send(prev, 1.0)\n    prev = str(i)",
    "n = 0\nwhile n < 2:\n    n += 1",
    "x = 'ab'[0]\ny = (1, 2)[1]\nr = {'a': 1}\nz = r['a']",
    "t = txs()\nk = 'to'\ny = t[0][k]",
    "b = balance()\ny = f'{b:.2f} {b} {None}'",
    "t = txs()\ny = t[0].get('to')",
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
        ("txs", 4),
        ("keep", 4),
    ]
    assert check.effects == Effects(writes=True, moves_funds=True, outward=True)
    assert TOOLS.check(parse_plan("b = balance()")).effects == READ_ONLY


def test_inference_that_does_not_settle_assumes_nothing() -> None:
    # In a loop a read may come before its binding; one round of inference
    # settles one link of this chain, so a long one never settles.
    def chain(links: int) -> str:
        body = "\n".join(f"    x{i + 1} = x{i}" for i in reversed(range(links)))
        return f"x0 = 'ten'\nfor i in range(2):\n{body}\ny = send('a', x{links})"

    assert static_errors(chain(70)) == []
    assert static_errors(chain(3)) == [(C.BAD_ARGUMENTS, 6, "send")]


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
        "1", "2.5", "'a'", "None", "True", "(1, 2)", "('a', 'b')", "[1]",
        "{'amount': 1.0, 'to': 'a'}", "a", "b", "balance()", "txs()", "txs(n=1)",
        "txs(1)[0]", "a.amount", "a.to", "a[0]", "a['to']", "b[0].to", "a.get('to')",
        "send('a', 1.0)", "send(a, b)", "send(to=a, amount=b)", "tag('a')", "tag(a)",
        "note(a)", "note(text=a)", "keep(a)", "len(a)", "str(a)", "int(a)", "float(a)",
        "sorted(a)", "range(2)", "f'{a}'", "a.lower()", "a.split()", "not a", "a == b",
        "a + b", "-a", "a is None", "min(a, b)", "round(a)", "q",
    ]
)  # fmt: skip
"""Expressions with no branch inside: every part of them runs."""


@st.composite
def straight_plans(draw: st.DrawFn) -> str:
    """Plans without branches, loops, conditional expressions or ``and``/``or``."""
    lines = []
    for _ in range(draw(st.integers(1, 5))):
        name = draw(st.sampled_from(["a", "b", "x"]))
        shape = draw(st.sampled_from(["{n} = {v}", "{n} = {v}", "{n} += 1"]))
        lines.append(shape.format(n=name, v=draw(EXPRESSIONS)))
    return "\n".join(lines)


@given(straight_plans())
def test_a_straight_plan_that_runs_is_never_refused(plan: str) -> None:
    if run_error(plan) is None:
        assert static_errors(plan) == []


@given(straight_plans())
def test_a_reported_statement_fails_when_it_runs(plan: str) -> None:
    # Every statement runs in order until one fails, so the run stops at the
    # first reported statement or before it.
    errors = static_errors(plan)
    if errors:
        failure = run_error(plan)
        assert failure is not None
        assert failure[1] is not None
        assert failure[1] <= errors[0][1]  # type: ignore[operator]
