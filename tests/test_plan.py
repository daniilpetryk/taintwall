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
        "x = '\u043f\u0440\u0438\u0432\u0435\u0442 \uff21'",
        "x = f'\u00e9{a}'",
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
        "x = f(_internal=1)",
        "x = f(__class__=1)",
        "\uff58 = 1",
        "x = \uff52\uff45\uff41\uff44_file()",
        "x = r.\u0430mount",
        "x = f(\u0430mount=1)",
        "x = (y := 1)",
        "x = {1: 2}",
        "x = {a: 2}",
        "x = a is b",
        "async def f(): pass",
        "async for x in y:\n    pass",
        "for x in y:\n    pass\nelse:\n    pass",
        "while x:\n    pass\nelse:\n    pass",
        "for a, b in y:\n    pass",
        "for r.x in y:\n    pass",
        "for _x in y:\n    pass",
        "break",
        "continue",
        "if x:\n    break",
        "while x:\n    pass\nbreak",
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


@pytest.mark.parametrize("op", ["-", "not ", "~"])
def test_parser_exhaustion_is_typed(op: str) -> None:
    # Review round 1: CPython's parser raised a raw MemoryError here.
    source = "x = " + op * ((MAX_PLAN_CHARS - 10) // len(op)) + "1"
    assert code_of(source) in {PlanErrorCode.TOO_DEEP, PlanErrorCode.UNSUPPORTED}


def test_accepts_moderate_nesting() -> None:
    parse_plan("x = " + "-" * (MAX_PLAN_DEPTH // 2) + "1")


def test_error_details_come_from_the_plan_only() -> None:
    with pytest.raises(PlanError) as err:
        parse_plan("import os")
    assert err.value.detail == "Import"
    assert str(err.value) == "unsupported at line 1 (Import)"


@pytest.mark.parametrize(
    "source",
    [
        "if x:\n    y = 1\nelif z:\n    y = 2\nelse:\n    pass",
        "for x in y:\n    if x:\n        break\n    continue",
        "while x:\n    for y in z:\n        break\n    break",
    ],
)
def test_accepts_control_flow(source: str) -> None:
    parse_plan(source)


def test_exit_outside_a_loop_reports_its_line() -> None:
    with pytest.raises(PlanError) as err:
        parse_plan("x = 1\nif x:\n    continue")
    assert (err.value.line, err.value.detail) == (3, "exit outside loop")


@pytest.mark.parametrize(
    ("source", "detail"),
    [
        ("x = 1\nfor x in y:\n    pass\nelse:\n    pass", "loop else"),
        ("x = 1\nwhile x:\n    pass\nelse:\n    pass", "loop else"),
        ("x = 1\nfor a, b in y:\n    pass", "loop target"),
    ],
)
def test_loop_shapes_report_line_and_detail(source: str, detail: str) -> None:
    with pytest.raises(PlanError) as err:
        parse_plan(source)
    assert (err.value.line, err.value.detail) == (2, detail)
