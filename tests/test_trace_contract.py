# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The provenance trace contract: one exact event per syntax node.

The viewer (stage 6) and later policies read the trace, so for every node the
event's kind, line, detail and inputs are pinned. ``inputs`` point at the
events of the values the node read, in evaluation order.
"""

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import EventKind, Interpreter, RunResult, TraceEvent
from taintwall.core.labels import MAX_VALUE_DEPTH, Label, Labeled, Source
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.core.values import Record

MINT = Mint()
TOOL = Label.of(Source.tool("info"))


def info() -> Labeled[object]:
    return Labeled(Record.of({"name": Labeled("usdc", TOOL)}), TOOL)


def echo(item: Labeled[object]) -> Labeled[object]:
    return Labeled(item.value, TOOL)


def run(source: str) -> RunResult:
    interpreter = Interpreter({"info": info, "echo": echo}, requester=MINT.owner(), mint=MINT)
    return interpreter.run(parse_plan(source))


def last_event(source: str) -> tuple[TraceEvent, dict[int, TraceEvent]]:
    """The event that made ``x``, and every value's first event by id."""
    trace = run("a = 1\nb = 2\n" + source).trace
    first: dict[int, TraceEvent] = {}
    for event in trace:
        first.setdefault(event.value_id, event)
    assigned = trace[-1]
    assert (assigned.kind, assigned.detail) == (EventKind.ASSIGN, "x")
    return first[assigned.inputs[0]], first


K = EventKind


@pytest.mark.parametrize(
    ("source", "kind", "detail", "input_details"),
    [
        ("x = 5", K.LITERAL, "", []),
        ("x = a + b", K.OPERATION, "Add", ["", ""]),
        ("x = -a", K.OPERATION, "USub", [""]),
        ("x = not a", K.OPERATION, "Not", [""]),
        ("x = a < b", K.OPERATION, "Compare", ["", ""]),
        ("x = a and b", K.OPERATION, "And", ["", ""]),
        ("x = 0 or b", K.OPERATION, "Or", ["", ""]),
        ("x = len((a, b))", K.OPERATION, "len", ["sequence"]),
        ("x = info()", K.CALL, "info", []),
        ("x = echo(a)", K.CALL, "echo", [""]),
        ("x = echo(item=b)", K.CALL, "echo", [""]),
        ("x = info().name", K.FIELD, "name", ["info"]),
        ("x = info()['name']", K.FIELD, "", ["info", ""]),
        ("x = (a, b)[0]", K.INDEX, "", ["sequence", ""]),
        ("x = (a, b)", K.BUILD, "sequence", ["", ""]),
        ("x = [a]", K.BUILD, "sequence", [""]),
        ("x = {'f': a}", K.BUILD, "record", [""]),
        ("x = f'{a}-{b}'", K.OPERATION, "format", ["", ""]),
    ],
)
def test_one_exact_event_per_node(
    source: str, kind: EventKind, detail: str, input_details: list[str]
) -> None:
    event, first = last_event(source)
    assert (event.kind, event.line, event.detail) == (kind, 3, detail)
    assert [first[value_id].detail for value_id in event.inputs] == input_details


def test_inputs_point_at_variables_by_their_value() -> None:
    event, first = last_event("x = a + b")
    assert [first[value_id].line for value_id in event.inputs] == [1, 2]


def test_format_carries_the_literal_label() -> None:
    # The f-string's text is the plan's, so the plan is a source even when
    # every interpolated value comes from a tool.
    event, _ = last_event("n = info().name\nx = f'{n}'")
    assert MINT.plan() in event.label.sources
    assert Source.tool("info") in event.label.sources


def test_nesting_error_points_at_the_build() -> None:
    source = "a = (1,)\n" + "a = (a,)\n" * MAX_VALUE_DEPTH
    with pytest.raises(PlanError) as err:
        run(source)
    assert (err.value.code, err.value.line) == (PlanErrorCode.VALUE_TOO_LARGE, MAX_VALUE_DEPTH + 1)
