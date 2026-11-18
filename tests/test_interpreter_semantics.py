# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Evaluation results and labels that mutation testing found unpinned.

Each test fixes one behavior a mutant changed without failing the suite:
false comparisons, record equality, indexing strings and plain tool tuples,
record truthiness, nesting inside tool values, and typed errors that used to
escape as raw exceptions.
"""

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import Interpreter, RunResult, Tool
from taintwall.core.labels import MAX_VALUE_DEPTH, Label, Labeled, Source
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.core.values import Record

MINT = Mint()
TOOL = Source.tool("data")
ELEMENT = Source.tool("element")


def data() -> Labeled[object]:
    """A plain tuple (no per-element labels), as a simple tool returns it."""
    return Labeled(("x", "y"), Label.of(TOOL))


def deep() -> Labeled[object]:
    """A tool value nested ``MAX_VALUE_DEPTH - 4`` plain levels deep."""
    value: object = 1
    for _ in range(MAX_VALUE_DEPTH - 4):
        value = (value,)
    return Labeled(value, Label.of(TOOL))


def run(source: str) -> RunResult:
    tools: dict[str, Tool] = {"data": data, "deep": deep}
    return Interpreter(tools, requester=MINT.owner(), mint=MINT).run(parse_plan(source))


def value(source: str) -> object:
    return run(source).variables["x"].value


def code(source: str) -> PlanErrorCode:
    with pytest.raises(PlanError) as err:
        run(source)
    return err.value.code


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x = 1 > 2", False),
        ("x = 2 > 1", True),
        ("x = 1 < 3 < 2", False),
        ("x = 1 < 2 < 3", True),
        ("x = 3 < 2 < 1", False),
        ("x = 'z' in ('x', 'y')", False),
        ("x = {'a': 1} == {'a': 2}", False),
        ("x = {'a': 1} == {'a': 1}", True),
        ("x = {'a': 1} == {'b': 1}", False),
    ],
)
def test_comparisons(source: str, *, expected: bool) -> None:
    assert value(source) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x = {'a': 1} or 5", "record"),
        ("x = {} or 5", 5),
        ("x = not {'a': 1}", False),
        ("x = not {}", True),
        ("x = () or 5", 5),
        ("x = (0,) or 5", "tuple"),
    ],
)
def test_truthiness_reads_only_emptiness(source: str, expected: object) -> None:
    result = value(source)
    if expected == "record":
        assert isinstance(result, Record)
    elif expected == "tuple":
        assert type(result) is tuple
    else:
        assert result == expected


def test_indexing_a_string() -> None:
    result = run("s = data()[1]\nx = s[0]").variables["x"]
    assert result.value == "y"
    assert TOOL in result.label.sources


def test_indexing_a_plain_tool_tuple_uses_the_container_label() -> None:
    result = run("x = data()[0]").variables["x"]
    assert result.value == "x"
    assert TOOL in result.label.sources


def test_nesting_counts_plain_levels_inside_tool_values() -> None:
    # The tool value is within the limit; wrapping it in the plan crosses it.
    run("a = deep()\n" + "a = (a,)\n" * 3)
    assert code("a = deep()\n" + "a = (a,)\n" * 6) is PlanErrorCode.VALUE_TOO_LARGE


@pytest.mark.parametrize(
    "source",
    [
        "x = 2 * None",
        "x = None * 2",
        "x = (1,) * 1.5",
        "x = {'a': 1}[5]",
        "x = abs('a')",
        "x = 1 < 'a'",
    ],
)
def test_bad_operand_types_are_typed_errors(source: str) -> None:
    assert code(source) is PlanErrorCode.TYPE_MISMATCH


def test_assign_events_carry_the_value_id() -> None:
    trace = run("x = 5").trace
    assert trace[-1].value_id == trace[0].value_id == trace[-1].inputs[0]


def test_plan_keeps_its_source() -> None:
    source = "x = 1\n"
    assert parse_plan(source).source == source
