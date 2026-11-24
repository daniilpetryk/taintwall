# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""What the checker infers of each kind of expression, without running the plan."""

import pytest

from taintwall.core.plan import parse_plan
from taintwall.tools import READ_ONLY, Known, ListOf, Maybe, Param, RecordOf, Scalar, ToolSpec
from taintwall.tools.infer import MAX_ROUNDS, Static, infer

TX = RecordOf("Transaction", (("amount", Scalar.FLOAT), ("to", Scalar.STR)))
SPECS = {
    "txs": ToolSpec("txs", "T.", (), ListOf(TX), READ_ONLY),
    "one": ToolSpec("one", "O.", (Param("n", Scalar.INT),), TX, READ_ONLY),
}
ANY, STR, INT, FLOAT, BOOL = Scalar.ANY, Scalar.STR, Scalar.INT, Scalar.FLOAT, Scalar.BOOL


def x_of(plan: str) -> Static:
    return infer(parse_plan(plan).tree, SPECS)["x"]


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        # Constants and names.
        ("x = 1", Known(1)),
        ("x = 'a'", Known("a")),
        ("x = None", Known(None)),
        ("y = 'a'\nx = y", Known("a")),
        # Literals.
        ("x = (1, 2)", ListOf(INT)),
        ("x = [1, 2]", ListOf(INT)),
        ("x = (1, 'a')", ListOf(ANY)),
        ("x = ()", ListOf(ANY)),
        ("x = {'a': 1, 'b': 'c'}", RecordOf("dict", (("a", INT), ("b", STR)))),
        # Calls.
        ("x = txs()", ListOf(TX)),
        ("x = one(1)", TX),
        ("x = len('a')", INT),
        ("x = bool(1)", BOOL),
        ("x = str(1)", STR),
        ("x = int('1')", INT),
        ("x = float('1')", FLOAT),
        ("x = range(2)", ListOf(INT)),
        ("x = any((True,))", BOOL),
        ("x = all((True,))", BOOL),
        ("x = abs(1)", ANY),
        ("x = sorted((2, 1))", ListOf(INT)),
        ("x = sorted('xy')", ANY),
        # Methods.
        ("x = 'a'.lower()", STR),
        ("x = 'a'.upper()", STR),
        ("x = 'a'.strip()", STR),
        ("x = 'a'.replace('a', 'b')", STR),
        ("x = ','.join(('a',))", STR),
        ("x = 'a'.startswith('a')", BOOL),
        ("x = 'a'.endswith('a')", BOOL),
        ("x = 'a b'.split()", ListOf(STR)),
        ("x = one(1).get('amount')", Maybe(FLOAT)),
        ("x = {'a': 1}.get('a')", Maybe(INT)),
        ("x = one(1).get('nope')", ANY),
        ("k = 'amount'\nx = one(1).get(k)", ANY),
        ("x = txs().get('amount')", ANY),
        # Fields and elements.
        ("x = one(1).amount", FLOAT),
        ("x = one(1).nope", ANY),
        ("x = txs()[0]", TX),
        ("x = one(1)['to']", STR),
        ("k = 'to'\nx = one(1)[k]", ANY),
        ("x = 'ab'[0]", STR),
        ("s = str(1)\nx = s[0]", STR),
        ("x = 1[0]", ANY),
        ("x = len('a')[0]", ANY),
        # Operators.
        ("x = 1 < 2", BOOL),
        ("x = not 1", BOOL),
        ("x = -1", ANY),
        ("x = 1 + 2", ANY),
        ("x = f'{1}'", STR),
        ("x = 1 if True else 1", Known(1)),
        ("x = 1 if True else 2", INT),
        ("x = 1 if True else 'a'", ANY),
        ("x = 1 and 2", ANY),
        # Names and calls inside other expressions.
        ("x = q", ANY),
        ("y = 1\nx = (y, y)", ListOf(INT)),
        ("x = (one(1),)", ListOf(TX)),
        ("y = 'a'\nx = {'a': y}", RecordOf("dict", (("a", STR),))),
        ("x = {'a': one(1)}", RecordOf("dict", (("a", TX),))),
        ("y = 1\nx = y if True else y", Known(1)),
        ("x = one(1) if True else one(2)", TX),
        ("y = (2, 1)\nx = sorted(y)", ListOf(INT)),
        ("x = sorted(txs())", ListOf(TX)),
        ("y = one(1)\nx = y.amount", FLOAT),
        ("y = txs()\nx = y[0]", TX),
        ("y = one(1)\nx = y.get('to')", Maybe(STR)),
        ("x = one(1).to.lower()", STR),
        # Several assignments, and loops.
        ("x = 1\nx = 2", INT),
        ("x = 1\nx = 1", Known(1)),
        ("x = 1\nx = 'a'", ANY),
        ("x = True\nx = 1", ANY),
        ("x = 1.0\nx = 1", ANY),
        ("x = 1\nx += 1", ANY),
        ("for x in txs():\n    pass", TX),
        ("for x in 'ab':\n    pass", STR),
        ("for x in (1, 2):\n    pass", INT),
        ("for x in len('a'):\n    pass", ANY),
        ("x = y\ny = 1", Known(1)),
    ],
)
def test_infers(plan: str, expected: Static) -> None:
    assert x_of(plan) == expected


def test_a_chain_longer_than_the_rounds_is_unknown() -> None:
    chain = "\n".join(f"x{i + 1} = x{i}" for i in range(MAX_ROUNDS + 2))
    types = infer(parse_plan(f"{chain}\nx0 = 1").tree, SPECS)
    assert set(types.values()) == {ANY}
    short = infer(parse_plan("x2 = x1\nx1 = x0\nx0 = 1").tree, SPECS)
    assert short["x2"] == Known(1)


def test_constants_of_different_types_differ() -> None:
    assert Known(value=True) != Known(1)
    assert Known(1.0) != Known(1)
    assert Known(1) == Known(1)
    assert len({Known(value=True), Known(1), Known(1.0)}) == 3
