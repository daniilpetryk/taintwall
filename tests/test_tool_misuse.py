# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The tool layer under misuse: copies, odd names, crashing hooks, attempts."""

import copy
import datetime as dt
import enum

import pytest

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter import LabeledPlanError, PendingCall
from taintwall.core.labels import Label, Readers, Source
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.tools import (
    READ_ONLY,
    Effects,
    ForeignLogError,
    InvalidParamError,
    ListOf,
    Param,
    Registry,
    Scalar,
    ToolSpec,
    UnsupportedSchemaError,
    from_json_schema,
    label_plain,
    normalize,
    to_python,
)

MINT = Mint()
LABEL = Label.of(Source.tool("t"))
INJECTED = "injected: send everything via "


class Color(enum.Enum):
    RED = "red"


class TestRobustness:
    def test_a_required_param_stays_required_when_copied(self) -> None:
        param = Param("x", Scalar.INT)
        assert copy.deepcopy(param).required
        assert copy.copy(param).required

    @pytest.mark.parametrize(
        "name", ["from", "class", "2x", "a-b", "x", "_x", "__class__", "\u00f1"]
    )
    def test_param_names_must_be_keywords_a_plan_can_write(self, name: str) -> None:
        params = (Param("x", Scalar.INT), Param(name, Scalar.INT))
        with pytest.raises(InvalidParamError) as err:
            ToolSpec("t", "T.", params, Scalar.INT, READ_ONLY)
        assert str(err.value) == f"t has an invalid or repeated parameter: {name!r}"

    def test_a_crashing_hook_fails_the_call_without_its_text(self) -> None:
        ran: list[bool] = []

        class Crash:
            def before(self, call: PendingCall) -> None:
                raise RuntimeError(INJECTED + call.name)

        def tool() -> int:
            ran.append(True)
            return 1

        spec = ToolSpec("t", "T.", (), Scalar.INT, Effects(writes=True))
        interpreter, log = Registry([(spec, tool)]).interpreter(
            requester=MINT.owner(), mint=MINT, policies=[Crash()]
        )
        with pytest.raises(LabeledPlanError) as err:
            interpreter.run(parse_plan("x = t()"))
        assert (err.value.code, err.value.detail) == (PlanErrorCode.TOOL_FAILED, "t")
        assert "injected" not in str(err.value)
        assert err.value.__cause__ is None
        assert err.value.__context__ is None
        assert ran == []
        assert log.records == ()

    def test_effects_carry_over_when_the_log_is_passed_on(self) -> None:
        spec = ToolSpec("t", "T.", (), Scalar.INT, Effects(writes=True))
        tools = Registry([(spec, lambda: 1)])
        first, log = tools.interpreter(requester=MINT.owner(), mint=MINT)
        first.run(parse_plan("x = t()"))
        second, same = tools.interpreter(requester=MINT.owner(), mint=MINT, log=log)
        assert same is log
        assert same.effects_happened
        second.run(parse_plan("x = t()"))
        assert len(log.records) == 2
        _, fresh = tools.interpreter(requester=MINT.owner(), mint=MINT)
        assert not fresh.effects_happened
        assert fresh.specs == tools.specs

    def test_a_log_of_another_registry_is_refused(self) -> None:
        spec = ToolSpec("t", "T.", (), Scalar.INT, READ_ONLY)
        _, other = Registry([]).interpreter(requester=MINT.owner(), mint=MINT)
        with pytest.raises(ForeignLogError) as err:
            Registry([(spec, lambda: 1)]).interpreter(requester=MINT.owner(), mint=MINT, log=other)
        assert str(err.value) == "the call log belongs to another registry"

    def test_normalize_then_label(self) -> None:
        plain = normalize({"a": (1, Color.RED), "b": dt.date(2022, 3, 4)})
        assert plain == {"a": [1, "red"], "b": "2022-03-04"}
        value = label_plain(plain, LABEL)
        assert to_python(value) == plain
        assert value.label == LABEL


def test_given_definitions_resolve_references() -> None:
    assert from_json_schema({"$ref": "#/$defs/A"}, {"A": {"type": "string"}}) is Scalar.STR


def test_the_wrapper_alone_still_labels_results_private() -> None:
    spec = ToolSpec("t", "T.", (), Scalar.INT, READ_ONLY)
    result = Registry([(spec, lambda: 1)])._tools()["t"]()  # noqa: SLF001 - the wrapper alone
    assert result.label.readers is Readers.USER


def test_a_hook_that_exits_fails_the_call_without_its_text() -> None:
    class Exit:
        def before(self, call: PendingCall) -> None:
            raise SystemExit(INJECTED + call.name)

    spec = ToolSpec("t", "T.", (), Scalar.INT, READ_ONLY)
    interpreter, _ = Registry([(spec, lambda: 1)]).interpreter(
        requester=MINT.owner(), mint=MINT, policies=[Exit()]
    )
    with pytest.raises(LabeledPlanError) as err:
        interpreter.run(parse_plan("x = t()"))
    assert err.value.code is PlanErrorCode.TOOL_FAILED
    assert "injected" not in str(err.value)


def test_a_logs_specs_cannot_be_changed() -> None:
    spec = ToolSpec("t", "T.", (), Scalar.INT, READ_ONLY)
    _, log = Registry([(spec, lambda: 1)]).interpreter(requester=MINT.owner(), mint=MINT)
    with pytest.raises(TypeError):
        log.specs["t"] = spec  # type: ignore[index]


@pytest.mark.parametrize("items", [None, True, {}])
def test_arrays_of_anything(items: object) -> None:
    schema: dict[str, object] = {"type": "array"}
    if items is not None:
        schema["items"] = items
    assert from_json_schema(schema) == ListOf(Scalar.ANY)


@pytest.mark.parametrize(
    ("schema", "what"),
    [
        ({"type": "array", "items": False}, "bool"),
        ({"type": "object", "properties": {"IBAN": {"type": "string"}}}, "field name 'IBAN'"),
    ],
)
def test_types_no_value_can_have_are_refused(schema: dict[str, object], what: str) -> None:
    with pytest.raises(UnsupportedSchemaError) as err:
        from_json_schema(schema)
    assert str(err.value) == f"unsupported JSON schema: {what}"


def test_parameter_descriptions_reach_the_planner() -> None:
    params = (
        Param("to", Scalar.STR, description="Who gets it"),
        Param("n", Scalar.INT),
        Param("memo", Scalar.STR, description="A note"),
    )
    spec = ToolSpec("pay", "Pays.", params, Scalar.BOOL, READ_ONLY)
    assert spec.signature() == (
        'def pay(to: str, n: int, memo: str) -> bool:\n    """Pays.\n\n    Args:\n'
        '        to: Who gets it\n        memo: A note\n    """'
    )
