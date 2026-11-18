# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The plan language: what the static checker accepts and rejects."""

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.plan import MAX_PLAN_CHARS, MAX_PLAN_DEPTH, parse_plan

INVOICE_PLAN = """
invoice = read_file("invoice.pdf")
details = query_qllm("Extract payment recipient and amount", data=invoice)
tx = build_transfer(token="USDC", to=details.recipient, amount=details.amount)
send_transaction(tx)
"""


def code_of(source: str) -> PlanErrorCode:
    with pytest.raises(PlanError) as err:
        parse_plan(source)
    return err.value.code


def test_accepts_the_design_example() -> None:
    plan = parse_plan(INVOICE_PLAN)
    assert len(plan.tree.body) == 4


@pytest.mark.parametrize(
    "source",
    [
        "x = (1, [2, 3], {'a': 4})",
        "x = a.b.c[0]",
        "x = f'{a} and {b}'",
        "x = -a + b * 2 // 3 % 4 / 5",
        "x = a < b <= c and not d or e in f and g not in h",
        "x = tool(1, key=2)",
        "x = None",
    ],
)
def test_accepts_the_language(source: str) -> None:
    parse_plan(source)


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "from os import path",
        "def f(): pass",
        "x = lambda: 1",
        "class A: pass",
        "if x: pass",
        "for x in y: pass",
        "while True: pass",
        "x = 1 if y else 2",
        "x = [a for a in b]",
        "x = {1, 2}",
        "x = a.__class__",
        "x = __import__('os')",
        "x = _private",
        "x = a._private",
        "x = text.upper()",
        "x = items[1:2]",
        "x = f'{a!r}'",
        "x = f'{a:>1000000000}'",
        "x = b'bytes'",
        "x = ...",
        "x = 1j",
        "x = 2 ** 3",
        "x = a | b",
        "x += 1",
        "a = b = 1",
        "a, b = 1, 2",
        "x.y = 1",
        "x[0] = 1",
        "x: int = 1",
        "del x",
        "global x",
        "try:\n    pass\nexcept Exception:\n    pass",
        "with x:\n    pass",
        "assert x",
        "raise X",
        "x = f(*args)",
        "x = f(**kwargs)",
        "x = (y := 1)",
        "x = {1: 2}",
        "x = {a: 2}",
        "x = a is b",
        "async def f(): pass",
    ],
)
def test_rejects_everything_outside_the_language(source: str) -> None:
    assert code_of(source) is PlanErrorCode.UNSUPPORTED


def test_syntax_error_has_a_line() -> None:
    with pytest.raises(PlanError) as err:
        parse_plan("x = 1\ny = (\n")
    assert err.value.code is PlanErrorCode.SYNTAX
    assert err.value.line is not None


def test_null_byte_is_a_syntax_error() -> None:
    assert code_of("x = 1\x00") is PlanErrorCode.SYNTAX


def test_rejects_oversized_plans_before_parsing() -> None:
    assert code_of("x = 1\n" * (MAX_PLAN_CHARS // 6 + 1)) is PlanErrorCode.TOO_LARGE


def test_rejects_deep_nesting() -> None:
    deep = "x = " + "-" * (MAX_PLAN_DEPTH + 5) + "1"
    assert code_of(deep) is PlanErrorCode.TOO_DEEP


def test_accepts_moderate_nesting() -> None:
    parse_plan("x = " + "-" * (MAX_PLAN_DEPTH // 2) + "1")


def test_error_details_come_from_the_plan_only() -> None:
    with pytest.raises(PlanError) as err:
        parse_plan("import os")
    assert err.value.detail == "Import"
    assert str(err.value) == "unsupported at line 1 (Import)"
