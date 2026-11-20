# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""AgentDojo banking functions as taintwall tools, run by the interpreter offline."""

from types import SimpleNamespace

import pytest
from agentdojo.task_suite.load_suites import get_suite
from pydantic import BaseModel, Field
from registry import registry, spec_for

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter import LabeledPlanError, RunResult
from taintwall.core.labels import Readers, Source
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan
from taintwall.core.values import Record
from taintwall.tools import CallLog, ListOf, RecordOf, Scalar

SUITE = get_suite("v1.2.2", "banking")
MINT = Mint()


def run(plan: str) -> tuple[RunResult, CallLog]:
    tools = registry(SUITE, SUITE.load_and_inject_default_environment({}), "banking")
    interpreter, log = tools.interpreter(requester=MINT.owner(), mint=MINT)
    return interpreter.run(parse_plan(plan)), log


def test_every_banking_function_has_a_spec() -> None:
    specs = {function.name: spec_for(function, "banking") for function in SUITE.tools}
    assert specs["send_money"].effects.moves_funds
    assert specs["send_money"].effects.outward
    assert specs["schedule_transaction"].effects.outward
    assert not specs["update_password"].effects.outward

    assert specs["get_balance"].effects.read_only
    assert specs["get_balance"].returns is Scalar.FLOAT
    transactions = specs["get_most_recent_transactions"]
    assert isinstance(transactions.returns, ListOf)
    assert isinstance(transactions.returns.item, RecordOf)
    assert transactions.returns.item.name == "Transaction"
    assert "(n: int = 100) -> list[Transaction]" in transactions.signature()


def test_a_read_returns_labeled_records() -> None:
    result, log = run("t = get_most_recent_transactions(2)\nx = t[0].amount\ny = t[0]")
    x = result.variables["x"]
    assert type(x.value) is float
    assert Source.tool("get_most_recent_transactions") in x.label.sources
    assert x.label.readers is Readers.USER
    assert isinstance(result.variables["y"].value, Record)
    assert not log.effects_happened


def test_an_ill_typed_argument_stops_before_the_call() -> None:
    with pytest.raises(LabeledPlanError) as err:
        run("x = send_money('GB29', 'ten', 'rent', '2022-01-01')")
    assert (err.value.code, err.value.detail) == (PlanErrorCode.BAD_ARGUMENTS, "send_money")


def test_a_payment_is_an_effect() -> None:
    _, log = run("x = send_money('GB29', 10.0, 'rent', '2022-01-01')")
    assert log.effects_happened
    assert [record.name for record in log.records] == ["send_money"]


def test_a_function_error_is_a_tool_failure_without_its_text() -> None:
    with pytest.raises(LabeledPlanError) as err:
        run("x = update_scheduled_transaction(999999)")
    expected = (PlanErrorCode.TOOL_FAILED, "update_scheduled_transaction")
    assert (err.value.code, err.value.detail) == expected
    assert "999999" not in str(err.value)


def test_defaults_are_the_real_ones_factories_included() -> None:
    class Arguments(BaseModel):
        tags: list[str] = Field(default_factory=lambda: ["rent"])
        n: int = 3

    function = SimpleNamespace(
        name="tagged", description="Tagged.", parameters=Arguments, return_type=int
    )
    signature = spec_for(function, "banking").signature()
    assert "(tags: list[str] = ['rent'], n: int = 3) -> int" in signature


def test_parameters_are_named_by_alias() -> None:
    class Arguments(BaseModel):
        sender: str = Field(alias="from")

    function = SimpleNamespace(
        name="aliased", description="Aliased.", parameters=Arguments, return_type=str
    )
    assert "(from: str) -> str" in spec_for(function, "banking").signature()


def test_parameters_are_named_by_validation_alias() -> None:
    class Arguments(BaseModel):
        sender: str = Field(validation_alias="to")

    function = SimpleNamespace(
        name="aliased", description="Aliased.", parameters=Arguments, return_type=str
    )
    assert "(to: str) -> str" in spec_for(function, "banking").signature()


def test_an_unlisted_function_has_every_effect() -> None:
    class Arguments(BaseModel):
        pass

    function = SimpleNamespace(
        name="new_thing", description="New.", parameters=Arguments, return_type=int
    )
    effects = spec_for(function, "banking").effects
    assert (effects.writes, effects.moves_funds, effects.outward) == (True, True, True)
