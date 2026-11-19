# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Property tests: no explicit flow escapes its label.

Random straight-line expressions over variables that come from tools with
distinct labels. Two properties:

* Noninterference: if a variable's source is not in the result's label, then
  changing that variable's value does not change the result. This is the
  precise statement; a syntactic "the expression mentions it" would be too
  coarse, because labels are per element and per field on purpose.
* Every trace event's label covers the labels of its inputs (except ``BUILD``,
  where each element keeps its own label inside the container).

Runs that raise a ``PlanError`` are skipped: an error that depends on data is
an implicit flow (the termination channel), covered with the pc stack.
"""

from collections.abc import Mapping

from hypothesis import given
from hypothesis import strategies as st

from taintwall.core.errors import PlanError
from taintwall.core.interpreter import EventKind, Interpreter, RunResult, Tool
from taintwall.core.labels import Label, Labeled, Source, join_all
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.core.values import Record

MINT = Mint()
NAMES = ("a", "b", "c", "d")
BASE: Mapping[str, object] = {"a": 7, "b": -3, "c": "tok", "d": 0}
ALTERED: Mapping[str, object] = {"a": 5, "b": 11, "c": "zz", "d": 2}

_BINARY = ("+", "-", "*", "//", "%", "/", "==", "!=", "<", "<=", "in", "and", "or")


def _expressions() -> st.SearchStrategy[str]:
    leaves = st.sampled_from([*NAMES, "1", "2", "'x'", "True", "None"])

    def extend(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
        items = st.lists(inner, min_size=1, max_size=3).map(", ".join)
        return st.one_of(
            st.builds("({} {} {})".format, inner, st.sampled_from(_BINARY), inner),
            st.builds("({}{})".format, st.sampled_from(["-", "not "]), inner),
            items.map("({},)".format),
            st.builds("(({},)[{}])".format, items, st.sampled_from(["0", "a", "d"])),
            st.builds("f'<{{{}}}>'".format, inner),
            st.builds("{}(({},))".format, st.sampled_from(["len", "sum", "min", "max"]), inner),
            st.builds("({{'f': {}, 'g': {}}}).f".format, inner, inner),
            st.builds("({} if {} else {})".format, inner, inner, inner),
            st.builds("({} is None)".format, inner),
            st.builds("{}({})".format, st.sampled_from(["str", "round", "int", "float"]), inner),
            st.builds("({}).{}()".format, inner, st.sampled_from(["lower", "strip", "split"])),
            st.builds("{{'k': {}}}.get({}, {})".format, inner, inner, inner),
            st.builds("sorted(({},))".format, items),
        )

    return st.recursive(leaves, extend, max_leaves=8)


def _run(expression: str, values: Mapping[str, object]) -> RunResult | None:
    def source_tool(name: str) -> Tool:
        def tool() -> Labeled[object]:
            return Labeled(values[name], Label.of(Source.tool(f"src_{name}")))

        return tool

    tools = {f"src_{name}": source_tool(name) for name in NAMES}
    prelude = "".join(f"{name} = src_{name}()\n" for name in NAMES)
    interpreter = Interpreter(tools, requester=MINT.owner(), mint=MINT)
    try:
        return interpreter.run(parse_plan(f"{prelude}x = {expression}\n"))
    except PlanError:
        return None


def _deep(value: Labeled[object]) -> Label:
    inner = value.value
    nested: list[Label] = []
    if isinstance(inner, tuple):
        nested = [_deep(item) for item in inner if isinstance(item, Labeled)]
    elif isinstance(inner, Record):
        nested = [_deep(field) for _, field in inner.fields]
    return join_all(value.label, *nested)


def _plain(value: Labeled[object]) -> object:
    inner = value.value
    if isinstance(inner, tuple):
        return tuple(_plain(item) if isinstance(item, Labeled) else item for item in inner)
    if isinstance(inner, Record):
        return {name: _plain(field) for name, field in inner.fields}
    return (type(inner), inner)


@given(_expressions())
def test_unlabeled_variables_do_not_interfere(expression: str) -> None:
    base = _run(expression, BASE)
    if base is None:
        return
    result = base.variables["x"]
    sources = _deep(result).sources
    for name in NAMES:
        if Source.tool(f"src_{name}") in sources:
            continue
        altered = _run(expression, {**BASE, name: ALTERED[name]})
        if altered is None:  # termination channel, see the module docstring
            continue
        assert _plain(altered.variables["x"]) == _plain(result), (expression, name)


@given(_expressions())
def test_every_event_covers_its_inputs(expression: str) -> None:
    run = _run(expression, BASE)
    if run is None:
        return
    by_id: dict[int, Label] = {}
    for event in run.trace:
        if event.kind is not EventKind.BUILD:
            for input_id in event.inputs:
                assert by_id[input_id].sources <= event.label.sources, (expression, event)
        by_id.setdefault(event.value_id, event.label)
