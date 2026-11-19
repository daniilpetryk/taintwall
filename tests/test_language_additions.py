# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Language version 2: what planners keep writing (docs/planner-plans.md).

String methods, ``record.get``, number formats, ``is None``, ``a if c else
b``, ``+=`` and new builtins, each with the labels of what its result
depends on, typed errors, and sizes checked before a value is built.
"""

from collections.abc import Mapping

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import (
    EventKind,
    ImplicitFlows,
    Interpreter,
    LabeledPlanError,
    Limits,
    RunResult,
    Tool,
)
from taintwall.core.interpreter.methods import HANDLERS
from taintwall.core.labels import Label, Labeled, Source
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.core.syntax import STRING_METHODS
from taintwall.core.values import Record

MINT = Mint()
OWNER = MINT.owner()
TEXT = Source.tool("text")
SEP = Source.tool("sep")
FLAG = Source.tool("flag")
ROW = Source.tool("row")
FIELD = Source.principal("discord:666")
NUMBER = Source.tool("number")
LITERAL = Label.of(MINT.plan(), OWNER)


def _constant(value: object, source: Source) -> Tool:
    return lambda: Labeled(value, Label.of(source))


def _row() -> Labeled[object]:
    return Labeled(Record.of({"name": Labeled("Bob", Label.of(FIELD))}), Label.of(ROW))


class Counter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Labeled[object]:
        self.calls += 1
        return Labeled(1, Label.of(Source.tool("side")))


TOOLS: Mapping[str, Tool] = {
    "text": _constant("  Rent: 1200 EUR  ", TEXT),
    "sep": _constant(",", SEP),
    "flag": _constant(value=True, source=FLAG),
    "off": _constant(value=False, source=FLAG),
    "row": _row,
    "number": _constant(1234.5, NUMBER),
    "nothing": _constant(None, NUMBER),
    "items": _constant((3, 1, 2), NUMBER),
}


def run(
    source: str,
    *,
    tools: Mapping[str, Tool] | None = None,
    limits: Limits | None = None,
    flows: ImplicitFlows = ImplicitFlows.STRICT,
) -> RunResult:
    interpreter = Interpreter(
        {**TOOLS, **(tools or {})}, requester=OWNER, mint=MINT, limits=limits, flows=flows
    )
    return interpreter.run(parse_plan(source))


def x(source: str, **kwargs: object) -> Labeled[object]:
    return run(source, **kwargs).variables["x"]  # type: ignore[arg-type]


def failure(source: str, **kwargs: object) -> LabeledPlanError:
    with pytest.raises(LabeledPlanError) as err:
        run(source, **kwargs)  # type: ignore[arg-type]
    return err.value


class TestStringMethods:
    def test_the_parser_and_the_interpreter_know_the_same_methods(self) -> None:
        assert set(HANDLERS) == STRING_METHODS

    @pytest.mark.parametrize(
        ("call", "value"),
        [
            ("lower()", "  rent: 1200 eur  "),
            ("upper()", "  RENT: 1200 EUR  "),
            ("strip()", "Rent: 1200 EUR"),
            ("strip(' R')", "ent: 1200 EU"),
            ("startswith('  R')", True),
            ("endswith('X')", False),
            ("replace('EUR', 'USD')", "  Rent: 1200 USD  "),
            ("split()", ("Rent:", "1200", "EUR")),
            ("split(':')", ("  Rent", " 1200 EUR  ")),
        ],
    )
    def test_values_follow_python(self, call: str, value: object) -> None:
        result = x(f"x = text().{call}")
        plain = (
            tuple(getattr(item, "value", item) for item in result.value)
            if isinstance(result.value, tuple)
            else result.value
        )
        assert plain == value

    def test_the_result_carries_receiver_and_arguments(self) -> None:
        result = x("x = text().replace(' ', sep())")
        assert {TEXT, SEP} <= result.label.sources

    def test_join_reads_every_element(self) -> None:
        tools = {"parts": lambda: Labeled((Labeled("a", Label.of(FIELD)), "b"), Label.of(ROW))}
        result = x("x = sep().join(parts())", tools=tools)
        assert result.value == "a,b"
        assert {SEP, ROW, FIELD} <= result.label.sources

    def test_split_parts_take_the_string_label(self) -> None:
        assert TEXT in x("p = text().split()\nx = p[0]").label.sources

    @pytest.mark.parametrize(
        ("source", "code"),
        [
            ("x = number().lower()", PlanErrorCode.TYPE_MISMATCH),
            ("x = text().lower(1)", PlanErrorCode.BAD_ARGUMENTS),
            ("x = text().startswith()", PlanErrorCode.BAD_ARGUMENTS),
            ("x = text().replace('a')", PlanErrorCode.BAD_ARGUMENTS),
            ("x = text().split('')", PlanErrorCode.TYPE_MISMATCH),
            ("x = sep().join(text())", PlanErrorCode.BAD_ARGUMENTS),
            ("x = sep().join()", PlanErrorCode.BAD_ARGUMENTS),
            ("x = sep().join((1, 2))", PlanErrorCode.TYPE_MISMATCH),
            ("x = text().get('a')", PlanErrorCode.TYPE_MISMATCH),
        ],
    )
    def test_misuse_is_typed(self, source: str, code: PlanErrorCode) -> None:
        err = failure(source)
        assert err.code is code
        assert TEXT in err.label.sources or SEP in err.label.sources or NUMBER in err.label.sources

    def test_replace_checks_the_size_before_building(self) -> None:
        err = failure("x = text().replace('', 'abcdefgh')", limits=Limits(max_string=100))
        assert err.code is PlanErrorCode.VALUE_TOO_LARGE

    def test_join_checks_the_size_before_building(self) -> None:
        err = failure("p = ('aaaa',) * 30\nx = sep().join(p)", limits=Limits(max_string=100))
        assert err.code is PlanErrorCode.VALUE_TOO_LARGE

    def test_a_method_on_a_nested_value_reads_every_wrapper(self) -> None:
        tools = {"wrapped": lambda: Labeled(Labeled("Ab", Label.of(FIELD)), Label.of(ROW))}
        result = x("x = wrapped().lower()", tools=tools)
        assert result.value == "ab"
        assert {FIELD, ROW} <= result.label.sources

    def test_the_trace_names_the_method(self) -> None:
        trace = run("x = text().strip()").trace
        assert any(event.detail == ".strip" for event in trace)


class TestRecordGet:
    def test_a_present_field_reads_like_an_index(self) -> None:
        result = x("x = row().get('name', number())")
        assert result.value == "Bob"
        # The default is a traced input either way, so its label counts too.
        assert {FIELD, ROW, NUMBER} <= result.label.sources

    def test_a_missing_field_gives_the_default_labeled_by_the_record(self) -> None:
        result = x("x = row().get('age', 0)")
        assert result.value == 0
        assert ROW in result.label.sources
        assert FIELD not in result.label.sources

    def test_without_a_default_a_missing_field_is_none(self) -> None:
        assert x("x = row().get('age')").value is None

    def test_a_computed_key_adds_its_label(self) -> None:
        # Which field was read depends on the key, for r.get(k) as for r[k].
        tools = {"key": _constant("name", SEP)}
        for expression in ("row().get(key(), 0)", "row()[key()]"):
            result = x(f"x = {expression}", tools=tools)
            assert result.value == "Bob"
            assert SEP in result.label.sources

    def test_the_key_must_be_a_string(self) -> None:
        assert failure("x = row().get(1)").code is PlanErrorCode.BAD_ARGUMENTS


class TestBuiltins:
    @pytest.mark.parametrize(
        ("expression", "value"),
        [
            ("round(number())", 1234),
            ("round(number(), 0)", 1234.0),
            ("int(number())", 1234),
            ("int('42')", 42),
            ("float('2.5')", 2.5),
            ("str(number())", "1234.5"),
            ("bool(text())", True),
            ("any((0, 1))", True),
            ("all((0, 1))", False),
            ("range(3)", (0, 1, 2)),
            ("range(1, 7, 3)", (1, 4)),
        ],
    )
    def test_values_follow_python(self, expression: str, value: object) -> None:
        assert x(f"x = {expression}").value == value

    def test_sorted_keeps_each_elements_label(self) -> None:
        tools = {
            "pair": lambda: Labeled(
                (Labeled(2, Label.of(FIELD)), Labeled(1, Label.of(TEXT))), Label.of(ROW)
            )
        }
        result = run("s = sorted(pair())\nx = s[0]\ny = s", tools=tools).variables
        assert result["x"].value == 1
        assert TEXT in result["x"].label.sources
        # The order depends on every element, so the tuple carries all of them.
        assert {FIELD, TEXT, ROW} <= result["y"].label.sources

    def test_conversions_carry_their_argument(self) -> None:
        assert NUMBER in x("x = str(number())").label.sources

    def test_bool_unwraps_nested_values(self) -> None:
        tools = {"nested": lambda: Labeled(Labeled(0, Label.of(FIELD)), Label.of(ROW))}
        result = x("x = bool(nested())", tools=tools)
        assert result.value is False
        assert {FIELD, ROW} <= result.label.sources

    def test_bool_reads_only_the_container(self) -> None:
        tools = {"pair": lambda: Labeled((Labeled(0, Label.of(FIELD)),), Label.of(ROW))}
        result = x("x = bool(pair())", tools=tools)
        assert result.value is True
        assert FIELD not in result.label.sources

    @pytest.mark.parametrize(
        ("source", "code"),
        [
            ("x = str((1, 2))", PlanErrorCode.TYPE_MISMATCH),
            ("x = int(nothing())", PlanErrorCode.TYPE_MISMATCH),
            ("x = float(nothing())", PlanErrorCode.TYPE_MISMATCH),
            ("x = int('a')", PlanErrorCode.TYPE_MISMATCH),
            ("x = float('nan')", PlanErrorCode.TYPE_MISMATCH),
            ("x = float('1e999')", PlanErrorCode.VALUE_TOO_LARGE),
            ("x = range('3')", PlanErrorCode.TYPE_MISMATCH),
            ("x = range(1, 2, 0)", PlanErrorCode.TYPE_MISMATCH),
            ("x = sorted(text())", PlanErrorCode.TYPE_MISMATCH),
            ("x = sorted((1, 'a'))", PlanErrorCode.TYPE_MISMATCH),
            ("x = round()", PlanErrorCode.BAD_ARGUMENTS),
            ("x = range(1, 2, 3, 4)", PlanErrorCode.BAD_ARGUMENTS),
            ("x = len(text(), 1)", PlanErrorCode.BAD_ARGUMENTS),
            ("x = abs(x=1)", PlanErrorCode.BAD_ARGUMENTS),
        ],
    )
    def test_misuse_is_typed(self, source: str, code: PlanErrorCode) -> None:
        assert failure(source).code is code

    @pytest.mark.parametrize(
        "source",
        [
            "x = round(flag())",
            "x = abs(flag())",
            "x = round(1.5, flag())",
            "x = sum((1, flag()))",
            "x = sum(text())",
            "x = int(flag())",
            "x = float(flag())",
            "x = any(text())",
            "x = all(text())",
            "x = int('1_000')",
            "x = int(' 42')",
            "x = float('1_0.5')",
            "x = float('inf')",
        ],
    )
    def test_booleans_and_loose_text_are_not_numbers(self, source: str) -> None:
        err = failure(source)
        assert err.code is PlanErrorCode.TYPE_MISMATCH
        assert err.detail == source.split("=")[1].strip().split("(")[0]

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            ("x = int('-42')", -42),
            ("x = float('+2.5e3')", 2500.0),
            ("x = float('.5')", 0.5),
            ("x = float('5.')", 5.0),
            ("x = str(flag())", "True"),
            ("x = sum((1, 2.5))", 3.5),
        ],
    )
    def test_plain_numbers_still_convert(self, source: str, value: object) -> None:
        assert x(source).value == value

    @pytest.mark.parametrize(
        ("source", "detail"),
        [("x = int('a')", "int"), ("x = sorted((1, 'a'))", "sorted"), ("x = min(())", "min")],
    )
    def test_errors_inside_a_builtin_name_it(self, source: str, detail: str) -> None:
        assert failure(source).detail == detail

    def test_max_and_min_compare_anything_comparable(self) -> None:
        assert x("x = max((1, 2))").value == 2
        assert x("x = min('b', 'a')").value == "a"

    def test_a_large_int_still_converts_within_the_digit_limit(self) -> None:
        # 2000 digits: the size estimate must not refuse what Python accepts.
        source = (
            "n = 10000000000\nm = n * n * n * n * n * n * n * n * n * n\n"
            "k = m * m * m * m * m * m * m * m * m * m * m * m * m * m * m * m * m * m * m * m\n"
            "x = str(k)"
        )
        assert len(x(source, limits=Limits(max_int_bits=10000)).value) == 2001  # type: ignore[arg-type]

    def test_float_of_an_int_past_the_float_range_is_too_large(self) -> None:
        source = (
            "n = 10000000000\nm = n * n * n * n * n * n * n * n * n * n\n"
            "k = m * m * m * m\nx = float(k)"
        )
        err = failure(source, limits=Limits(max_int_bits=2000))
        assert (err.code, err.line, err.detail) == (PlanErrorCode.VALUE_TOO_LARGE, 4, "float")

    def test_str_of_an_int_past_the_digit_limit_is_too_large(self) -> None:
        source = (
            "n = 10000000000\nm = n * n * n * n * n * n * n * n * n * n\n"
            "k = m * m * m * m * m * m * m * m * m * m\nk = k * k * k * k * k\nx = str(k)"
        )
        err = failure(source, limits=Limits(max_int_bits=30000))
        assert (err.code, err.detail) == (PlanErrorCode.VALUE_TOO_LARGE, "str")

    def test_str_of_none_is_none_like_an_f_string(self) -> None:
        assert x("x = str(nothing())").value == "None"

    def test_a_range_too_long_to_measure_is_too_large(self) -> None:
        # len() of a range past a C ssize_t raises OverflowError in Python.
        big = "100000000000000000000"
        err = failure(f"n = {big} * {big} * {big}\nx = range(n)")
        assert (err.code, err.line, err.detail) == (PlanErrorCode.VALUE_TOO_LARGE, 2, "range")

    def test_a_number_too_large_for_a_fixed_point_format_is_typed(self) -> None:
        source = (
            "n = 10000000000\nm = n * n * n * n * n * n * n * n * n * n\n"
            "k = m * m * m * m * m * m * m * m * m * m * m * m\nx = f'{k:.2f}'"
        )
        err = failure(source, limits=Limits(max_int_bits=4096))
        assert (err.code, err.line) == (PlanErrorCode.VALUE_TOO_LARGE, 4)

    def test_range_checks_its_length_before_building(self) -> None:
        err = failure("x = range(1000000000)", limits=Limits(max_items=1000))
        assert err.code is PlanErrorCode.VALUE_TOO_LARGE

    def test_builtin_names_are_reserved(self) -> None:
        for name in ("str", "range", "sorted", "any"):
            err = failure(f"{name} = 1")
            assert (err.code, err.detail) == (PlanErrorCode.UNSUPPORTED, name)


class TestIdentityAndFormats:
    def test_is_none_compares_the_plain_value(self) -> None:
        result = run("a = nothing() is None\nb = number() is not None\nc = row() is None").variables
        assert (result["a"].value, result["b"].value, result["c"].value) == (True, True, False)
        assert NUMBER in result["a"].label.sources

    @pytest.mark.parametrize(
        ("spec", "text"), [(".2f", "1234.50"), (",.2f", "1,234.50"), (".0f", "1234")]
    )
    def test_fixed_point_formats(self, spec: str, text: str) -> None:
        result = x(f'x = f"{{number():{spec}}} EUR"')
        assert result.value == f"{text} EUR"
        assert NUMBER in result.label.sources

    def test_a_format_needs_a_number(self) -> None:
        assert failure('x = f"{text():.2f}"').code is PlanErrorCode.TYPE_MISMATCH
        assert failure('x = f"{flag():.2f}"').code is PlanErrorCode.TYPE_MISMATCH


class TestConditionalAndShortCircuit:
    def test_only_the_chosen_branch_runs(self) -> None:
        side = Counter()
        result = x("x = side() if off() else 2", tools={"side": side})
        assert (result.value, side.calls) == (2, 0)

    def test_the_result_carries_the_test_in_both_modes(self) -> None:
        for flows in ImplicitFlows:
            assert FLAG in x("x = 1 if flag() else 2", flows=flows).label.sources

    def test_the_chosen_value_keeps_its_nested_labels(self) -> None:
        result = x("x = row() if flag() else 0")
        assert isinstance(result.value, Record)
        name = result.value.get("name")
        assert name is not None
        assert FIELD in name.label.sources

    def test_a_tool_in_a_branch_runs_under_the_test(self) -> None:
        trace = run("x = side() if flag() else 0", tools={"side": Counter()}).trace
        call = next(
            event for event in trace if event.kind is EventKind.CALL and event.detail == "side"
        )
        assert call.control is not None
        assert FLAG in call.control.sources

    def test_a_later_operand_of_and_runs_under_the_earlier_ones(self) -> None:
        for source in ("x = flag() and side()", "x = off() or side()"):
            trace = run(source, tools={"side": Counter()}).trace
            call = next(e for e in trace if e.kind is EventKind.CALL and e.detail == "side")
            assert call.control is not None
            assert FLAG in call.control.sources

    def test_the_first_operand_runs_under_no_pc(self) -> None:
        trace = run("x = flag() and 1").trace
        first = next(e for e in trace if e.detail == "flag")
        assert first.control is None


class TestAugmentedAssignment:
    @pytest.mark.parametrize(("op", "value"), [("+=", 7), ("-=", 3), ("*=", 10)])
    def test_updates_the_variable(self, op: str, value: int) -> None:
        assert x(f"x = 5\nx {op} 2").value == value

    def test_tuples_grow(self) -> None:
        result = x("x = ()\nfor i in (1, 2):\n    x += (i,)")
        assert [item.value for item in result.value] == [1, 2]  # type: ignore[attr-defined]

    def test_the_result_carries_both_sides(self) -> None:
        assert NUMBER in x("x = 1\nx += number()").label.sources

    def test_an_unassigned_target_is_unknown(self) -> None:
        err = failure("x += 1")
        assert (err.code, err.detail) == (PlanErrorCode.UNKNOWN_NAME, "x")

    def test_a_tool_name_is_rejected_before_the_value_runs(self) -> None:
        side = Counter()
        err = failure("text += side()", tools={"side": side})
        assert (err.code, side.calls) == (PlanErrorCode.UNSUPPORTED, 0)

    def test_the_trace_records_an_assignment(self) -> None:
        trace = run("x = 1\nx += 2").trace
        assert [event.kind for event in trace if event.line == 2][-1] is EventKind.ASSIGN

    def test_strict_flows_relabel_it_after_a_branch(self) -> None:
        result = x("x = 0\nif off():\n    x += 1")
        assert FLAG in result.label.sources
        assert result.label != LITERAL


class TestEdges:
    """Exact boundaries and error details, so each rule is pinned."""

    @pytest.mark.parametrize(
        ("source", "detail"),
        [
            ("x = a is 1", "is"),
            ("x //= 2", "augmented assignment"),
            ("x.y += 1", "augmented assignment"),
            ("x = s.lower(k=1)", "method keyword"),
            ("x = f(1)(2)", "call target"),
            ("x = s.pop()", "method call"),
        ],
    )
    def test_rejections_name_the_rule_and_line(self, source: str, detail: str) -> None:
        with pytest.raises(PlanError) as err:
            parse_plan(f"y = 1\n{source}")
        assert (err.value.code, err.value.line, err.value.detail) == (
            PlanErrorCode.UNSUPPORTED,
            2,
            detail,
        )

    @pytest.mark.parametrize(
        ("source", "code", "detail"),
        [
            ("x = bool()", PlanErrorCode.BAD_ARGUMENTS, "bool"),
            ("x = bool(1, 2)", PlanErrorCode.BAD_ARGUMENTS, "bool"),
            ("x = int()", PlanErrorCode.BAD_ARGUMENTS, "int"),
            ("x = str(1, 2)", PlanErrorCode.BAD_ARGUMENTS, "str"),
            ("x = sorted()", PlanErrorCode.BAD_ARGUMENTS, "sorted"),
            ("x = sorted((1,), (2,))", PlanErrorCode.BAD_ARGUMENTS, "sorted"),
            ("x = range()", PlanErrorCode.BAD_ARGUMENTS, "range"),
            ("x = str((1, 2))", PlanErrorCode.TYPE_MISMATCH, "str"),
            ("x = range('3')", PlanErrorCode.TYPE_MISMATCH, "range"),
            ("x = sorted(text())", PlanErrorCode.TYPE_MISMATCH, "sorted"),
            ("x = row().get(1)", PlanErrorCode.BAD_ARGUMENTS, "get"),
            ("x = row().get()", PlanErrorCode.BAD_ARGUMENTS, "get"),
            ("x = number().lower()", PlanErrorCode.TYPE_MISMATCH, "lower"),
            ("x = sep().join((1, 2))", PlanErrorCode.TYPE_MISMATCH, "join"),
            ("x = sep().join(1)", PlanErrorCode.BAD_ARGUMENTS, "join"),
            ('x = f"{text():.2f}"', PlanErrorCode.TYPE_MISMATCH, "format"),
            ("x = text().strip('a', 'b')", PlanErrorCode.BAD_ARGUMENTS, "strip"),
            ("x = text().split('a', 'b')", PlanErrorCode.BAD_ARGUMENTS, "split"),
            ("x = text().replace('a', 'b', 'c')", PlanErrorCode.BAD_ARGUMENTS, "replace"),
            ("x = text().lower('a')", PlanErrorCode.BAD_ARGUMENTS, "lower"),
            ("x = text().upper('a')", PlanErrorCode.BAD_ARGUMENTS, "upper"),
            ("x = text().endswith(1)", PlanErrorCode.BAD_ARGUMENTS, "endswith"),
            ("x = text().endswith()", PlanErrorCode.BAD_ARGUMENTS, "endswith"),
            ("x = text().endswith('a', 'b')", PlanErrorCode.BAD_ARGUMENTS, "endswith"),
            ("x = text().startswith('a', 'b')", PlanErrorCode.BAD_ARGUMENTS, "startswith"),
        ],
    )
    def test_errors_carry_their_code_and_detail(
        self, source: str, code: PlanErrorCode, detail: str
    ) -> None:
        err = failure(source)
        assert (err.code, err.line, err.detail) == (code, 1, detail)

    @pytest.mark.parametrize(
        ("expression", "length"),
        [
            ("'ab'.replace('', 'xy')", 8),  # an empty match before each character and at the end
            ("'abab'.replace('b', 'XYZ')", 8),
            ("','.join(('ab', 'cd', 'ef'))", 8),
            ("','.join(('ab',))", 2),
        ],
    )
    def test_sizes_are_exact_before_building(self, expression: str, length: int) -> None:
        assert len(x(f"x = {expression}", limits=Limits(max_string=length)).value) == length  # type: ignore[arg-type]
        err = failure(f"x = {expression}", limits=Limits(max_string=length - 1))
        assert err.code is PlanErrorCode.VALUE_TOO_LARGE

    def test_range_length_is_exact(self) -> None:
        assert x("x = range(5)", limits=Limits(max_items=5)).value == (0, 1, 2, 3, 4)
        assert (
            failure("x = range(6)", limits=Limits(max_items=5)).code
            is PlanErrorCode.VALUE_TOO_LARGE
        )

    def test_a_nested_test_decides_on_its_innermost_value(self) -> None:
        tools = {
            "nested": lambda: Labeled(Labeled(value=False, label=Label.of(FIELD)), Label.of(FLAG))
        }
        result = x("x = 1 if nested() else 2", tools=tools)
        assert result.value == 2
        assert {FIELD, FLAG} <= result.label.sources

    def test_trace_events_of_the_new_expressions(self) -> None:
        trace = run("x = 1 if flag() else 2\ny = text().strip()").trace
        test = next(e for e in trace if e.detail == "flag")
        chosen = next(e for e in trace if e.kind is EventKind.LITERAL and e.line == 1)
        conditional = next(e for e in trace if e.detail == "IfExp")
        assert (conditional.kind, conditional.inputs) == (
            EventKind.OPERATION,
            (test.value_id, chosen.value_id),
        )
        receiver = next(e for e in trace if e.detail == "text")
        method = next(e for e in trace if e.detail == ".strip")
        assert (method.kind, method.inputs) == (EventKind.OPERATION, (receiver.value_id,))
