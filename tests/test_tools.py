# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The tool layer: types, specs, value conversion, registry, call log and hook."""

import dataclasses
import datetime as dt
import enum

import pytest

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter import Interpreter, LabeledPlanError, PendingCall, RunResult, Tool
from taintwall.core.interpreter.arguments import Arguments
from taintwall.core.labels import MAX_VALUE_DEPTH, Label, Labeled, Readers, Source
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.core.values import Record
from taintwall.tools import (
    READ_ONLY,
    ArgumentError,
    ConversionError,
    DuplicateToolError,
    Effects,
    ListOf,
    Maybe,
    Param,
    RecordOf,
    Registry,
    Scalar,
    ToolSpec,
    UnsupportedSchemaError,
    accepts,
    from_json_schema,
    render,
    to_labeled,
    to_python,
)

MINT = Mint()
LABEL = Label.of(Source.tool("t"))
POINT = RecordOf("Point", (("x", Scalar.INT), ("tag", Maybe(Scalar.STR))))


class TestSchema:
    @pytest.mark.parametrize(
        ("expected", "value", "ok"),
        [
            (Scalar.STR, "a", True),
            (Scalar.INT, True, False),
            (Scalar.INT, 3, True),
            (Scalar.FLOAT, 3, True),
            (Scalar.FLOAT, "3", False),
            (Scalar.BOOL, 1, False),
            (Scalar.NONE, None, True),
            (Scalar.ANY, object(), True),
            (Maybe(Scalar.STR), None, True),
            (Maybe(Scalar.STR), 1, False),
            (ListOf(Scalar.INT), [1, 2], True),
            (ListOf(Scalar.INT), [1, "2"], False),
            (ListOf(Scalar.INT), (1,), False),
            (POINT, {"x": 1, "tag": None}, True),
            (POINT, {"x": 1}, False),
            (POINT, {"x": "1", "tag": None}, False),
            (POINT, [1], False),
        ],
    )
    def test_accepts_plain_values_by_exact_type(
        self, expected: object, value: object, *, ok: bool
    ) -> None:
        assert accepts(expected, value) is ok  # type: ignore[arg-type]

    def test_renders_as_a_planner_reads_it(self) -> None:
        assert render(ListOf(Maybe(Scalar.FLOAT))) == "list[float | None]"
        assert render(POINT) == "Point"
        assert render(Scalar.ANY) == "object"

    def test_reads_json_schemas_with_references(self) -> None:
        schema = {
            "type": "array",
            "items": {"$ref": "#/$defs/Point"},
            "$defs": {
                "Point": {
                    "title": "Point",
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "tag": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    },
                }
            },
        }
        assert from_json_schema(schema) == ListOf(POINT)
        assert from_json_schema({"type": "object"}) == RecordOf("Record", ())
        assert from_json_schema({"type": "array"}) == ListOf(Scalar.ANY)

    @pytest.mark.parametrize(
        "schema",
        [
            {"type": "tuple"},
            {"$ref": "#/$defs/Missing"},
            {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            {"anyOf": {"type": "string"}},
            {"type": "array", "items": 1},
        ],
    )
    def test_refuses_what_it_cannot_express(self, schema: dict[str, object]) -> None:
        with pytest.raises(UnsupportedSchemaError):
            from_json_schema(schema)


SEND = ToolSpec(
    name="send",
    description="Send money.",
    params=(
        Param("to", Scalar.STR),
        Param("amount", Scalar.FLOAT),
        Param("memo", Maybe(Scalar.STR), None),
    ),
    returns=Scalar.BOOL,
    effects=Effects(moves_funds=True),
)


class TestSpec:
    def test_binds_positional_keyword_and_defaults(self) -> None:
        assert SEND.bind(("a",), {"amount": 2}) == {"to": "a", "amount": 2, "memo": None}

    @pytest.mark.parametrize(
        ("positional", "keyword"),
        [
            (("a", 1.0, None, "extra"), {}),
            (("a",), {}),
            (("a", 1.0), {"to": "b"}),
            (("a", 1.0), {"fee": 1}),
            (("a", "ten"), {}),
            (("a", 1.0, 5), {}),
        ],
    )
    def test_refuses_what_does_not_fit(
        self, positional: tuple[object, ...], keyword: dict[str, object]
    ) -> None:
        with pytest.raises(ArgumentError, match="send"):
            SEND.bind(positional, keyword)

    def test_signature_shows_types_defaults_and_description(self) -> None:
        assert SEND.signature() == (
            "def send(to: str, amount: float, memo: str | None = None) -> bool:\n"
            '    """Send money."""'
        )

    def test_effects(self) -> None:
        assert READ_ONLY.read_only
        for effects in (Effects(writes=True), Effects(moves_funds=True), Effects(outward=True)):
            assert not effects.read_only


class Color(enum.Enum):
    RED = "red"


@dataclasses.dataclass
class Model:
    """Like a pydantic model: ``model_dump`` gives its fields."""

    amount: float

    def model_dump(self) -> dict[str, object]:
        return {"amount": self.amount, "when": dt.date(2022, 3, 4), "color": Color.RED}


class TestValues:
    def test_results_are_labeled_in_every_part(self) -> None:
        value = to_labeled([Model(1.5), {"ok": True}], LABEL)
        first = value.value[0]  # type: ignore[index]
        assert isinstance(first.value, Record)
        amount = first.value.get("amount")
        assert amount == Labeled(1.5, LABEL)
        assert first.value.get("when") == Labeled("2022-03-04", LABEL)
        assert first.value.get("color") == Labeled("red", LABEL)

    @pytest.mark.parametrize("bad", [{"Bad Key": 1}, {1: 2}, b"bytes", {1, 2}, object()])
    def test_refuses_what_is_not_a_plan_value(self, bad: object) -> None:
        with pytest.raises(ConversionError):
            to_labeled(bad, LABEL)

    def test_refuses_nesting_past_the_limit(self) -> None:
        deep: object = 1
        for _ in range(MAX_VALUE_DEPTH + 1):
            deep = [deep]
        with pytest.raises(ConversionError, match="deep"):
            to_labeled(deep, LABEL)
        nested: object = 1
        for _ in range(MAX_VALUE_DEPTH):
            nested = [nested]
        to_labeled(nested, LABEL)

    def test_arguments_leave_as_plain_python(self) -> None:
        record = Record.of({"a": Labeled((Labeled(1, LABEL),), LABEL)})
        assert to_python(Labeled(Labeled(record, LABEL), LABEL)) == {"a": [1]}


def run(plan: str, tools: Registry) -> tuple[RunResult, object]:
    log = tools.call_log()
    interpreter = Interpreter(tools.tools(), requester=MINT.owner(), mint=MINT, hook=log)
    return interpreter.run(parse_plan(plan)), log


class TestRegistry:
    def make(self) -> tuple[Registry, list[dict[str, object]]]:
        sent: list[dict[str, object]] = []

        def send(**arguments: object) -> bool:
            sent.append(arguments)
            return True

        balance = ToolSpec("balance", "Balance.", (), Scalar.FLOAT, readers=Readers.USER)
        return Registry([(SEND, send), (balance, lambda: 12.5)]), sent

    def test_results_carry_the_tool_and_its_readers(self) -> None:
        tools, _ = self.make()
        result, log = run("x = balance()", tools)
        x = result.variables["x"]
        assert (x.value, x.label.readers) == (12.5, Readers.USER)
        assert Source.tool("balance") in x.label.sources
        assert not log.effects_happened  # type: ignore[attr-defined]

    def test_a_call_with_effects_is_recorded_under_its_pc(self) -> None:
        tools, sent = self.make()
        _, log = run("b = balance()\nif b > 1:\n    x = send('a', 1.0)", tools)
        assert sent == [{"to": "a", "amount": 1.0, "memo": None}]
        assert log.effects_happened  # type: ignore[attr-defined]
        record = log.records[-1]  # type: ignore[attr-defined]
        assert (record.name, record.line, record.effects.moves_funds) == ("send", 3, True)
        assert Source.tool("balance") in record.control.sources

    def test_a_bad_argument_stops_before_the_side_effect(self) -> None:
        tools, sent = self.make()
        with pytest.raises(LabeledPlanError) as err:
            run("x = send('a', 'ten')", tools)
        assert (err.value.code, err.value.line, err.value.detail) == (
            PlanErrorCode.BAD_ARGUMENTS,
            1,
            "send",
        )
        assert sent == []

    def test_without_a_log_the_wrapper_still_checks(self) -> None:
        tools, sent = self.make()
        interpreter = Interpreter(tools.tools(), requester=MINT.owner(), mint=MINT)
        with pytest.raises(LabeledPlanError) as err:
            interpreter.run(parse_plan("x = send('a', 'ten')"))
        assert err.value.code is PlanErrorCode.TOOL_FAILED
        assert sent == []

    def test_specs_keep_their_order_and_names_are_unique(self) -> None:
        tools, _ = self.make()
        assert list(tools.specs) == ["send", "balance"]
        with pytest.raises(DuplicateToolError, match="send"):
            Registry([(SEND, print), (SEND, print)])


class TestHook:
    def test_sees_arguments_labels_pc_and_line_before_the_call(self) -> None:
        seen: list[PendingCall] = []

        class Spy:
            def before(self, call: PendingCall) -> None:
                seen.append(call)

        def echo(value: Labeled[object]) -> Labeled[object]:
            return value

        plan = "a = src()\nif a:\n    x = echo(a)"

        def src() -> Labeled[object]:
            return Labeled(value=True, label=LABEL)

        tools: dict[str, Tool] = {"echo": echo, "src": src}
        Interpreter(tools, requester=MINT.owner(), mint=MINT, hook=Spy()).run(parse_plan(plan))
        first, second = seen
        assert (first.name, first.line, first.control) == ("src", 1, None)
        assert (second.name, second.line) == ("echo", 3)
        assert isinstance(second.arguments, Arguments)
        assert second.labels[0].covers(LABEL)
        assert second.control is not None
        assert Source.tool("t") in second.control.sources


class TestEdges:
    def test_references_inside_definitions_resolve_against_all_of_them(self) -> None:
        schema = {
            "$ref": "#/$defs/Outer",
            "$defs": {
                "Outer": {
                    "title": "Outer",
                    "type": "object",
                    "properties": {
                        "one": {"$ref": "#/$defs/Inner"},
                        "maybe": {"anyOf": [{"$ref": "#/$defs/Inner"}, {"type": "null"}]},
                        "many": {"type": "array", "items": {"$ref": "#/$defs/Inner"}},
                    },
                },
                "Inner": {
                    "title": "Inner",
                    "type": "object",
                    "properties": {"v": {"type": "integer"}},
                },
            },
        }
        inner = RecordOf("Inner", (("v", Scalar.INT),))
        assert from_json_schema(schema) == RecordOf(
            "Outer", (("one", inner), ("maybe", Maybe(inner)), ("many", ListOf(inner)))
        )

    @pytest.mark.parametrize(
        ("schema", "message"),
        [
            ({"type": "tuple"}, "tuple"),
            ({"$ref": "#/$defs/Missing"}, "#/$defs/Missing"),
            ({"$ref": "other.json#/Point", "$defs": {"Point": {}}}, "other.json#/Point"),
            ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, "anyOf"),
            ({"anyOf": {"type": "string"}}, "anyOf"),
            ({"type": "array", "items": 1}, "int"),
        ],
    )
    def test_schema_errors_say_what_was_refused(
        self, schema: dict[str, object], message: str
    ) -> None:
        with pytest.raises(UnsupportedSchemaError) as err:
            from_json_schema(schema)
        assert str(err.value) == f"unsupported JSON schema: {message}"

    def test_maybe_accepts_its_inner_type(self) -> None:
        assert accepts(Maybe(Scalar.STR), "a")

    def test_defaults_fill_in_with_their_value(self) -> None:
        spec = ToolSpec("t", "T.", (Param("n", Scalar.INT, 100),), Scalar.INT)
        assert spec.bind((), {}) == {"n": 100}

    @pytest.mark.parametrize(
        ("bad", "message"), [(b"x", "bytes"), ({"Bad": 1}, "a key that is not a field name")]
    )
    def test_conversion_errors_say_what_was_refused(self, bad: object, message: str) -> None:
        with pytest.raises(ConversionError) as err:
            to_labeled(bad, LABEL)
        assert str(err.value) == f"cannot convert a tool result: {message}"

    def test_records_count_towards_the_nesting_limit(self) -> None:
        nested: object = 1
        for _ in range(MAX_VALUE_DEPTH):
            nested = {"a": nested}
        to_labeled(nested, LABEL)
        with pytest.raises(ConversionError, match="deep"):
            to_labeled({"a": nested}, LABEL)

    def test_keyword_arguments_reach_the_tool_and_the_log(self) -> None:
        sent: list[dict[str, object]] = []

        def send(**arguments: object) -> bool:
            sent.append(arguments)
            return True

        _, log = run("x = send('a', amount=2.0, memo='rent')", Registry([(SEND, send)]))
        assert sent == [{"to": "a", "amount": 2.0, "memo": "rent"}]
        assert log.effects_happened  # type: ignore[attr-defined]
