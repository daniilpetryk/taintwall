# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Property test: under strict implicit flows, no flow escapes its label.

Random plans with ``if``/``else``, ``for``, ``while``, ``break`` and
``continue`` over variables that come from tools with distinct labels. The
outcome of a run is the final ``x`` or the failure (code and line).
Noninterference: if a variable's source is in neither run's outcome label
(the deep label of ``x``, or the failure's label), changing that variable
does not change the outcome. A run whose outcome label does contain it is
skipped; that is what the label is for. Whether a plan stops at all is the
only thing allowed to differ (termination-insensitive). The step and size
limits are small, so plans hit them, and two sources are tools called
inline: one fails and one returns a value too large when altered.
"""

from collections.abc import Mapping

from hypothesis import given
from hypothesis import strategies as st

from taintwall.core.interpreter import Interpreter, LabeledPlanError, Limits, Tool
from taintwall.core.labels import Label, Labeled, Source, join_all
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.core.values import Record

MINT = Mint()
NAMES = ("a", "b", "c", "d")
"""Sources read into variables at the start of every plan."""
CALLED = ("e", "f")
"""Sources called inline: ``src_e()`` fails when altered, ``src_f()`` grows too large."""
BASE: Mapping[str, object] = {"a": 7, "b": 0, "c": "tok", "d": True, "e": 1, "f": "s"}
ALTERED: Mapping[str, object] = {
    "a": 0,
    "b": 4,
    "c": "",
    "d": False,
    "e": "fail",
    "f": "s" * 40,
}
LIMITS = {"max_string": 30, "max_visits": 400}
WRAPPER = Label.of(Source.tool("wrapper"))
"""Outer label of ``d``, which arrives nested: ``Labeled(Labeled(d, src_d), wrapper)``."""
_LEAVES = (*NAMES, "x", "y", "i", "0", "1", "2", "'t'", "True", "src_e()", "src_f()")
_OPERATORS = ("+", "-", "//", "==", "<", "and", "or")

type Outcome = tuple[object, ...]


def _expressions() -> st.SearchStrategy[str]:
    def extend(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
        return st.one_of(
            st.builds("({} {} {})".format, inner, st.sampled_from(_OPERATORS), inner),
            st.builds("(not {})".format, inner),
        )

    return st.recursive(st.sampled_from(_LEAVES), extend, max_leaves=4)


@st.composite
def _block(draw: st.DrawFn, indent: int, depth: int, *, in_loop: bool) -> list[str]:
    kinds = (
        ["assign"] + (["if", "for", "while"] if depth < 2 else []) + (["exit"] if in_loop else [])
    )
    lines: list[str] = []
    pad = " " * indent
    for _ in range(draw(st.integers(1, 3))):
        kind = draw(st.sampled_from(kinds))
        test = draw(_expressions())
        if kind == "assign":
            lines.append(f"{pad}{draw(st.sampled_from(['x', 'y']))} = {test}")
        elif kind == "if":
            lines += [f"{pad}if {test}:", *draw(_block(indent + 4, depth + 1, in_loop=in_loop))]
            if draw(st.booleans()):
                lines += [f"{pad}else:", *draw(_block(indent + 4, depth + 1, in_loop=in_loop))]
        elif kind == "for":
            other = draw(_expressions())
            lines += [
                f"{pad}for i in ({test}, {other}):",
                *draw(_block(indent + 4, depth + 1, in_loop=True)),
            ]
        elif kind == "while":
            counter = f"w{indent}"
            lines += [f"{pad}{counter} = 0", f"{pad}while {counter} < 2 and {test}:"]
            lines += [
                f"{pad}    {counter} = {counter} + 1",
                *draw(_block(indent + 4, depth + 1, in_loop=True)),
            ]
        else:
            exit_kind = draw(st.sampled_from(["break", "continue"]))
            lines += [f"{pad}if {test}:", f"{pad}    {exit_kind}"]
    return lines


def _plans() -> st.SearchStrategy[str]:
    prelude = [f"{name} = src_{name}()" for name in NAMES] + ["x = 0"]
    return _block(0, 0, in_loop=False).map(lambda body: "\n".join(prelude + body) + "\n")


def _deep(value: Labeled[object]) -> Label:
    inner = value.value
    nested: list[Label] = []
    if isinstance(inner, tuple):
        nested = [_deep(item) for item in inner if isinstance(item, Labeled)]
    elif isinstance(inner, Record):
        nested = [_deep(field) for _, field in inner.fields]
    return join_all(value.label, *nested)


def _plain(value: object) -> object:
    if isinstance(value, Labeled):
        return _plain(value.value)
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    return (type(value), value)


def _outcome(plan: str, values: Mapping[str, object], steps: int) -> tuple[Outcome, Label]:
    def source_tool(name: str) -> Tool:
        def tool() -> Labeled[object]:
            if values[name] == "fail":
                raise RuntimeError
            value = Labeled(values[name], Label.of(Source.tool(f"src_{name}")))
            # ``d`` comes wrapped: its source is only on the inner value.
            return Labeled(value, WRAPPER) if name == "d" else value

        return tool

    tools = {f"src_{name}": source_tool(name) for name in (*NAMES, *CALLED)}
    limits = Limits(max_steps=steps, **LIMITS)
    interpreter = Interpreter(tools, requester=MINT.owner(), mint=MINT, limits=limits)
    try:
        x = interpreter.run(parse_plan(plan)).variables["x"]
    except LabeledPlanError as err:
        return ("failed", err.code, err.line), err.label
    return ("ok", _plain(x)), _deep(x)


@given(_plans(), st.integers(20, 300))
def test_strict_flows_do_not_interfere(plan: str, steps: int) -> None:
    base, base_label = _outcome(plan, BASE, steps)
    for name in (*NAMES, *CALLED):
        source = Source.tool(f"src_{name}")
        altered, altered_label = _outcome(plan, {**BASE, name: ALTERED[name]}, steps)
        if source in base_label.sources or source in altered_label.sources:
            continue
        assert altered == base, (plan, name)
