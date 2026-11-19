# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The plan census script: construct detection and rejection reasons."""

from plan_census import census, counts, report

OFFERED = frozenset({"get_balance", "get_most_recent_transactions", "answer", "query_reader"})

NATURAL = """\
import datetime
names = [t.subject.lower() for t in get_most_recent_transactions(10)]
total = 0
total += 1
answer(f"{total:.2f}")
"""
ALLOWED = 'x = get_balance()\nif x > 1:\n    answer(f"{x}")\n'


def test_counts_constructs_and_reasons() -> None:
    plans: list[dict[str, object]] = [
        {"code": NATURAL, "finish_reason": "length"},
        {"code": ALLOWED},
        {"code": None},
        {"code": "x = ("},
    ]
    result = census(plans, OFFERED)
    assert (result.plans, result.cut, result.valid, result.accepted) == (4, 1, 2, 1)
    features = result.features
    assert {"import", "comprehension", "x += 1", "method call", "f-string spec", "if"} <= set(
        features
    )
    assert features["if"] == 1
    assert result.rejections == {
        "unsupported: Import": 1,
        "no code block": 1,
        "not Python": 1,
    }
    assert result.methods == {"lower": 1}


def test_called_builtins_exclude_tools_and_own_functions() -> None:
    code = "def f(x):\n    return x\ny = round(f(get_balance()))\nanswer(str(y))\n"
    result = census([{"code": code}], OFFERED)
    assert result.builtins == {"round": 1, "str": 1}


def test_report_has_a_row_per_construct() -> None:
    text = report({"m/a": census([{"code": ALLOWED, "finish_reason": "stop"}], OFFERED)})
    assert "| accepted by parse_plan (95% CI) | 100% (21-100) |" in text
    assert "| if | 100% |" in text
    assert "| plans | 1 |" in text
    assert "| cut at the token limit | 0 |" in text


def test_counts_are_the_rates_parts_per_model() -> None:
    data = census([{"code": ALLOWED, "finish_reason": "stop"}, {"code": None}], OFFERED)
    assert counts({"b/m": data, "a/m": data}) == {
        "a/m": {"plans": 2, "cut": 0, "valid": 1, "accepted": 1},
        "b/m": {"plans": 2, "cut": 0, "valid": 1, "accepted": 1},
    }
