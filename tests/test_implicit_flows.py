# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Implicit flows: what ``if``, ``for``, ``while`` and failures reveal (Q2)."""

from collections.abc import Mapping

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import (
    EventKind,
    ImplicitFlows,
    Interpreter,
    InvalidFlowsError,
    LabeledPlanError,
    Limits,
    RunResult,
    Tool,
)
from taintwall.core.labels import Label, Labeled, Source
from taintwall.core.mint import Mint
from taintwall.core.plan import parse_plan

MINT = Mint()
OWNER = MINT.owner()
FLAG = Source.tool("flag")
OFF = Source.tool("off")
LIMIT = Source.tool("limit")
ITEMS = Source.tool("items")
ITEM = Source.principal("discord:666")
EMPTY = Source.tool("empty")
ZERO = Source.tool("zero")
STRICT, LENIENT = ImplicitFlows.STRICT, ImplicitFlows.LENIENT


def _constant(value: object, source: Source) -> Tool:
    def tool() -> Labeled[object]:
        return Labeled(value, Label.of(source))

    return tool


def _items() -> Labeled[object]:
    return Labeled(
        (Labeled("USDC", Label.of(ITEM)), Labeled("DAI", Label.of(ITEM))), Label.of(ITEMS)
    )


def _echo(value: Labeled[object]) -> Labeled[object]:
    return Labeled(value.value, Label.of(Source.tool("echo")))


TOOLS: Mapping[str, Tool] = {
    "flag": _constant(value=True, source=FLAG),
    "off": _constant(value=False, source=OFF),
    "limit": _constant(3, LIMIT),
    "items": _items,
    "empty": _constant((), EMPTY),
    "zero": _constant(0, ZERO),
    "echo": _echo,
    "raw": _constant((1, 2), ITEMS),
}


def run(source: str, flows: ImplicitFlows = STRICT, limits: Limits | None = None) -> RunResult:
    interpreter = Interpreter(TOOLS, requester=OWNER, mint=MINT, limits=limits, flows=flows)
    return interpreter.run(parse_plan(source))


def sources(source: str, name: str = "x", flows: ImplicitFlows = STRICT) -> frozenset[Source]:
    return run(source, flows).variables[name].label.sources


def failure(
    source: str, flows: ImplicitFlows = STRICT, limits: Limits | None = None
) -> LabeledPlanError:
    with pytest.raises(LabeledPlanError) as err:
        run(source, flows, limits)
    return err.value


class TestIf:
    def test_a_branch_taken_carries_its_condition(self) -> None:
        plan = "c = flag()\nif c:\n    x = 1"
        assert FLAG in sources(plan)
        assert FLAG not in sources(plan, flows=LENIENT)

    def test_a_branch_not_taken_still_reveals_its_condition(self) -> None:
        plan = "x = 0\nif off():\n    x = 1"
        assert run(plan).variables["x"].value == 0
        assert OFF in sources(plan)
        assert OFF not in sources(plan, flows=LENIENT)

    def test_else_and_elif_branches_carry_every_test_that_ran(self) -> None:
        plan = "if off():\n    x = 1\nelif flag():\n    x = 2\nelse:\n    x = 3"
        assert run(plan).variables["x"].value == 2
        assert {OFF, FLAG} <= sources(plan)

    def test_a_copy_inside_a_branch_carries_the_condition(self) -> None:
        plan = "y = 5\nif flag():\n    x = y"
        assert FLAG in sources(plan)
        assert FLAG not in sources(plan, "y")

    def test_variables_outside_the_block_are_untouched(self) -> None:
        plan = "y = 5\nif flag():\n    x = 1\nz = 2"
        assert FLAG not in sources(plan, "y")
        assert FLAG not in sources(plan, "z")

    def test_a_tool_called_in_a_branch_depends_on_the_condition(self) -> None:
        plan = "if flag():\n    x = echo(1)"
        assert FLAG in sources(plan)
        assert FLAG not in sources(plan, flows=LENIENT)

    def test_a_condition_reads_only_the_container_label(self) -> None:
        plan = "x = 0\nif items():\n    x = 1"
        assert ITEMS in sources(plan)
        assert ITEM not in sources(plan)

    def test_the_condition_does_not_leak_after_the_block(self) -> None:
        plan = "if flag():\n    y = 1\nx = 2"
        assert FLAG not in sources(plan)


class TestFor:
    def test_elements_carry_their_own_and_the_container_label(self) -> None:
        plan = "for t in items():\n    x = t"
        result = run(plan, LENIENT).variables["x"]
        assert result.value == "DAI"
        assert {ITEM, ITEMS} <= result.label.sources

    def test_a_count_depends_on_the_container_only_when_strict(self) -> None:
        plan = "x = 0\nfor t in items():\n    x = x + 1"
        assert run(plan).variables["x"].value == 2
        assert ITEMS in sources(plan)
        assert ITEM not in sources(plan)
        assert ITEMS not in sources(plan, flows=LENIENT)

    def test_unlabeled_elements_take_the_container_label(self) -> None:
        plan = "for t in (1, 2):\n    x = t"
        assert run(plan).variables["x"].value == 2
        assert MINT.plan() in sources(plan)

    def test_plain_elements_of_a_tool_tuple_take_the_container_label(self) -> None:
        result = run("for t in raw():\n    x = t").variables["x"]
        assert (result.value, result.label.sources) == (2, frozenset({ITEMS, Source.tool("raw")}))

    def test_each_element_is_an_index_event_of_the_tuple(self) -> None:
        trace = run("t = (1,)\nfor i in t:\n    pass").trace
        tuple_id = next(event.value_id for event in trace if event.kind is EventKind.ASSIGN)
        element = next(
            event for event in trace if event.line == 2 and event.kind is EventKind.INDEX
        )
        assert (element.detail, element.inputs) == ("", (tuple_id,))

    def test_a_loop_that_never_runs_lists_its_tuple(self) -> None:
        trace = run("y = 0\nfor t in empty():\n    y = t").trace
        source = next(event for event in trace if event.detail == "empty")
        upgrade = trace[-1]
        assert (upgrade.kind, upgrade.detail) == (EventKind.CONTROL, "y")
        assert source.value_id in upgrade.inputs

    def test_a_loop_over_a_non_tuple_is_a_type_mismatch(self) -> None:
        err = failure("x = 0\nfor t in limit():\n    x = t")
        assert (err.code, err.line) == (PlanErrorCode.TYPE_MISMATCH, 2)
        assert LIMIT in err.label.sources

    def test_the_target_must_not_be_a_tool_name(self) -> None:
        err = failure("for echo in (1,):\n    pass")
        assert (err.code, err.detail) == (PlanErrorCode.UNSUPPORTED, "echo")

    def test_every_iteration_costs_a_step(self) -> None:
        plan = "t = (1,) * 100\nfor i in t:\n    pass"
        err = failure(plan, limits=Limits(max_steps=50))
        assert (err.code, err.line) == (PlanErrorCode.STEP_LIMIT, 2)
        assert run(plan).steps == 105  # 4 to build t, 1 to read it, 100 iterations


class TestExits:
    def test_break_puts_its_condition_on_the_rest_of_the_loop(self) -> None:
        plan = "for i in (1, 2, 3):\n    if off():\n        break\n    x = i"
        assert run(plan).variables["x"].value == 3
        assert OFF in sources(plan)
        assert OFF not in sources(plan, flows=LENIENT)

    def test_continue_puts_its_condition_on_the_rest_of_the_loop(self) -> None:
        plan = "x = 0\nfor i in (1, 2):\n    if off():\n        continue\n    x = i"
        assert OFF in sources(plan)

    def test_statements_after_a_broken_loop_run(self) -> None:
        assert run("for i in (1, 2):\n    break\nx = 5").variables["x"].value == 5

    def test_a_skipped_assignment_after_a_break_reveals_the_break(self) -> None:
        plan = "y = 0\nfor i in (1, 2):\n    if flag():\n        break\n    y = 1"
        result = run(plan).variables["y"]
        assert result.value == 0
        assert FLAG in result.label.sources

    def test_break_ends_the_loop(self) -> None:
        plan = "x = 0\nfor i in (1, 2, 3):\n    x = i\n    if flag():\n        break"
        assert run(plan).variables["x"].value == 1

    def test_continue_skips_the_rest_of_the_body(self) -> None:
        plan = "x = 0\nfor i in (1, 2, 3):\n    if i == 2:\n        continue\n    x = x + i"
        assert run(plan).variables["x"].value == 4

    def test_every_enclosing_test_of_an_exit_counts(self) -> None:
        plan = "for i in (1, 2):\n    if flag():\n        if off():\n            break\n    x = i"
        assert {FLAG, OFF} <= sources(plan)

    def test_an_exit_of_an_inner_loop_does_not_reach_the_outer_one(self) -> None:
        plan = (
            "for i in (1, 2):\n    for j in (1,):\n        if off():\n            break\n    x = i"
        )
        assert OFF not in sources(plan)
        assert OFF in sources(plan, "j")

    def test_a_test_without_an_exit_stays_inside_its_block(self) -> None:
        plan = "for i in (1, 2):\n    if off():\n        y = 1\n    x = i"
        assert OFF not in sources(plan)


class TestWhile:
    def test_the_count_depends_on_every_test(self) -> None:
        plan = "x = 0\nwhile x < limit():\n    x = x + 1"
        assert run(plan).variables["x"].value == 3
        assert LIMIT in sources(plan)
        assert LIMIT not in sources(plan, flows=LENIENT)

    def test_break_leaves_the_loop(self) -> None:
        plan = "x = 0\nwhile True:\n    x = x + 1\n    if x == 2:\n        break"
        assert run(plan).variables["x"].value == 2

    def test_continue_goes_back_to_the_test(self) -> None:
        plan = (
            "x = 0\ny = 0\nwhile x < 3:\n    x = x + 1\n"
            "    if x == 2:\n        continue\n    y = y + x"
        )
        assert run(plan).variables["y"].value == 4

    def test_an_endless_loop_hits_the_step_limit(self) -> None:
        err = failure("while True:\n    pass", limits=Limits(max_steps=20))
        assert (err.code, err.line) == (PlanErrorCode.STEP_LIMIT, 1)

    def test_a_loop_that_never_runs_still_reveals_its_test(self) -> None:
        trace = run("x = 0\nwhile off():\n    x = 1").trace
        test = next(event for event in trace if event.detail == "off")
        upgrade = trace[-1]
        assert (upgrade.kind, upgrade.detail) == (EventKind.CONTROL, "x")
        assert test.value_id in upgrade.inputs
        assert OFF in upgrade.label.sources


class TestTermination:
    def test_a_failure_in_a_branch_carries_the_condition(self) -> None:
        plan = "if flag():\n    x = 1 // 0"
        err = failure(plan)
        assert (err.code, err.line) == (PlanErrorCode.DIVISION_BY_ZERO, 2)
        assert FLAG in err.label.sources
        assert FLAG not in failure(plan, LENIENT).label.sources

    def test_a_failure_carries_the_data_it_read_in_both_modes(self) -> None:
        for flows in ImplicitFlows:
            err = failure("y = 1\nx = y // zero()", flows)
            assert ZERO in err.label.sources

    def test_a_failure_carries_nested_labels(self) -> None:
        err = failure("t = items()\nx = t < (1, 2)")
        assert {ITEM, ITEMS} <= err.label.sources

    def test_a_failure_outside_branches_carries_the_plan_only(self) -> None:
        err = failure("x = 1 // 0")
        assert err.label == Label.of(MINT.plan(), OWNER)

    def test_a_variable_a_skipped_branch_would_have_set(self) -> None:
        err = failure("if off():\n    y = 1\nx = y")
        assert (err.code, err.line) == (PlanErrorCode.UNKNOWN_NAME, 3)
        assert OFF in err.label.sources
        assert OFF not in failure("if off():\n    y = 1\nx = y", LENIENT).label.sources

    def test_every_skipped_block_counts_for_a_missing_variable(self) -> None:
        err = failure("if off():\n    y = 1\nif limit() < 0:\n    y = 2\nx = y")
        assert {OFF, LIMIT} <= err.label.sources

    def test_a_failure_label_reads_shared_values_once(self) -> None:
        # A billion paths through shared tuples; the label walk must not
        # follow each of them after the run has already failed.
        plan = "t = (flag(),) * 1000\nu = (t,) * 1000\nv = (u,) * 1000\nx = v // 2"
        err = failure(plan)
        assert err.code is PlanErrorCode.TYPE_MISMATCH
        assert FLAG in err.label.sources

    def test_an_empty_loop_reveals_its_container(self) -> None:
        err = failure("for t in empty():\n    y = t\nx = y")
        assert EMPTY in err.label.sources

    def test_a_never_assigned_variable_carries_the_plan_only(self) -> None:
        assert failure("x = y").label == Label.of(MINT.plan(), OWNER)

    def test_the_failure_is_a_plan_error_without_a_chain(self) -> None:
        err = failure("if flag():\n    x = 1 // 0")
        assert isinstance(err, PlanError)
        assert err.__cause__ is None
        assert err.__context__ is None


class TestTrace:
    def test_events_record_the_pc_in_both_modes(self) -> None:
        for flows in ImplicitFlows:
            trace = run("y = 1\nif flag():\n    x = 2", flows).trace
            outside = [event for event in trace if event.line == 1]
            inside = [event for event in trace if event.line == 3]
            assert all(event.control is None for event in outside)
            assert all(
                event.control is not None and FLAG in event.control.sources for event in inside
            )

    def test_an_upgrade_after_a_block_is_a_control_event(self) -> None:
        trace = run("x = 0\nif off():\n    x = 1").trace
        test = next(event for event in trace if event.detail == "off")
        upgrade = trace[-1]
        assert (upgrade.kind, upgrade.detail, upgrade.line) == (EventKind.CONTROL, "x", 2)
        assert test.value_id in upgrade.inputs

    def test_a_copy_in_a_branch_is_a_control_event_named_after_it(self) -> None:
        trace = run("y = 5\nif flag():\n    x = y").trace
        control = [event for event in trace if event.kind is EventKind.CONTROL]
        assert [event.detail for event in control] == ["x"]

    def test_lenient_runs_record_no_control_events(self) -> None:
        trace = run("x = 0\nif off():\n    x = 1\ny = x", LENIENT).trace
        assert all(event.kind is not EventKind.CONTROL for event in trace)

    def test_an_already_dependent_value_is_not_relabeled(self) -> None:
        trace = run("if flag():\n    x = 1").trace
        assert all(event.kind is not EventKind.CONTROL for event in trace)


class TestMode:
    def test_strict_is_the_default(self) -> None:
        interpreter = Interpreter(TOOLS, requester=OWNER, mint=MINT)
        result = interpreter.run(parse_plan("x = 0\nif off():\n    x = 1"))
        assert OFF in result.variables["x"].label.sources

    @pytest.mark.parametrize("mode", ["lenient", "strict", "loose", True])
    def test_only_a_mode_member_is_accepted(self, mode: object) -> None:
        with pytest.raises(InvalidFlowsError) as err:
            Interpreter(TOOLS, requester=OWNER, mint=MINT, flows=mode)  # type: ignore[arg-type]
        assert str(err.value) == "flows must be an ImplicitFlows member"


def _nested() -> Labeled[object]:
    return Labeled(Labeled(value=False, label=Label.of(ITEM)), Label.of(FLAG))


def _secret(value: int) -> Tool:
    return _constant(value, LIMIT)


def _fails() -> Labeled[object]:
    raise RuntimeError


class TestFailureChannels:
    LOOP = "n = 0\nwhile n < limit():\n    n = n + 1\nx = 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1"

    def run_with(self, plan: str, tools: Mapping[str, Tool], **kwargs: object) -> RunResult:
        interpreter = Interpreter({**TOOLS, **tools}, requester=OWNER, mint=MINT, **kwargs)  # type: ignore[arg-type]
        return interpreter.run(parse_plan(plan))

    def test_a_step_limit_carries_every_condition_the_run_took(self) -> None:
        # Fewer iterations end normally; more hit the limit on line 4, so the
        # failure reveals the loop bound although line 4 never reads it.
        ok = self.run_with(self.LOOP, {"limit": _secret(1)}, limits=Limits(max_steps=40))
        assert ok.variables["x"].value == 8
        with pytest.raises(LabeledPlanError) as err:
            self.run_with(self.LOOP, {"limit": _secret(6)}, limits=Limits(max_steps=40))
        assert (err.value.code, err.value.line) == (PlanErrorCode.STEP_LIMIT, 4)
        assert LIMIT in err.value.label.sources

    def test_lenient_step_limits_carry_data_only(self) -> None:
        with pytest.raises(LabeledPlanError) as err:
            self.run_with(self.LOOP, {}, limits=Limits(max_steps=20), flows=LENIENT)
        assert LIMIT not in err.value.label.sources

    def test_the_result_lists_every_condition_the_steps_depend_on(self) -> None:
        result = self.run_with("x = 0\nif off():\n    x = 1\ny = 2", {})
        assert result.control is not None
        assert OFF in result.control.sources
        assert run("x = 1").control is None

    def test_a_failing_tool_carries_its_own_source(self) -> None:
        for flows in ImplicitFlows:
            with pytest.raises(LabeledPlanError) as err:
                self.run_with("y = 1\nx = probe(y)", {"probe": _fails}, flows=flows)
            assert err.value.code is PlanErrorCode.TOOL_FAILED
            assert Source.tool("probe") in err.value.label.sources

    def test_an_oversized_result_carries_its_label(self) -> None:
        tools = {"page": _constant("z" * 20, ITEM)}
        with pytest.raises(LabeledPlanError) as err:
            self.run_with("x = page()", tools, limits=Limits(max_string=10))
        assert err.value.code is PlanErrorCode.VALUE_TOO_LARGE
        assert {ITEM, Source.tool("page")} <= err.value.label.sources

    def test_settling_a_block_draws_on_the_read_budget(self) -> None:
        names = "\n".join(f"    v{index} = 1" for index in range(30))
        plan = f"x = 0\nif off():\n{names}\ny = 1"
        with pytest.raises(LabeledPlanError) as err:
            self.run_with(plan, {}, limits=Limits(max_visits=10))
        assert (err.value.code, err.value.line) == (PlanErrorCode.STEP_LIMIT, 2)
        assert OFF in err.value.label.sources


class TestNestedTruth:
    def test_if_decides_on_the_innermost_value(self) -> None:
        result = run_nested("x = 0\nif nested():\n    x = 1")
        assert result.variables["x"].value == 0
        assert {ITEM, FLAG} <= result.variables["x"].label.sources

    def test_while_decides_on_the_innermost_value(self) -> None:
        assert run_nested("x = 0\nwhile nested():\n    x = 1").variables["x"].value == 0

    def test_not_and_boolean_operators_agree(self) -> None:
        result = run_nested("x = not nested()\ny = nested() or 5")
        assert result.variables["x"].value is True
        assert ITEM in result.variables["x"].label.sources
        assert result.variables["y"].value == 5

    def test_a_loop_unwraps_a_nested_tuple(self) -> None:
        tools = {"wrapped": lambda: Labeled(Labeled((1, 2), Label.of(ITEM)), Label.of(FLAG))}
        interpreter = Interpreter({**TOOLS, **tools}, requester=OWNER, mint=MINT)
        x = interpreter.run(parse_plan("for t in wrapped():\n    x = t")).variables["x"]
        assert x.value == 2
        assert {ITEM, FLAG} <= x.label.sources


def run_nested(plan: str) -> RunResult:
    interpreter = Interpreter({**TOOLS, "nested": _nested}, requester=OWNER, mint=MINT)
    return interpreter.run(parse_plan(plan))


def _chain(depth: int) -> Tool:
    def tool() -> Labeled[object]:
        value: Labeled[object] = Labeled(1, Label.of(ITEM))
        for _ in range(depth):
            value = Labeled(value, Label.of(FLAG))
        return value

    return tool


class TestDeepWrappers:
    def test_a_deep_chain_compares_without_recursion(self) -> None:
        interpreter = Interpreter({**TOOLS, "deep": _chain(5000)}, requester=OWNER, mint=MINT)
        x = interpreter.run(parse_plan("x = deep() == 1")).variables["x"]
        assert x.value is True
        assert {ITEM, FLAG} <= x.label.sources

    def test_unwrapping_draws_on_the_read_budget(self) -> None:
        limits = Limits(max_visits=50)
        interpreter = Interpreter(
            {**TOOLS, "deep": _chain(100)}, requester=OWNER, mint=MINT, limits=limits
        )
        for plan in ("x = 0\nif deep():\n    x = 1", "x = not deep()"):
            with pytest.raises(LabeledPlanError) as err:
                interpreter.run(parse_plan(plan))
            assert err.value.code is PlanErrorCode.STEP_LIMIT


class TestCallOrder:
    def test_argument_labels_are_read_before_the_call(self) -> None:
        calls: list[object] = []

        def send(value: Labeled[object]) -> Labeled[object]:
            calls.append(value)
            return Labeled(value=True, label=Label.of(ITEM))

        plan = "t = (1,) * 1000\nu = (t,) * 1000\nx = send(u)"
        interpreter = Interpreter({**TOOLS, "send": send}, requester=OWNER, mint=MINT)
        with pytest.raises(LabeledPlanError) as err:
            interpreter.run(parse_plan(plan))
        assert (err.value.code, err.value.line) == (PlanErrorCode.STEP_LIMIT, 3)
        assert calls == []


def test_a_step_limit_carries_conditions_found_only_inside_wrappers() -> None:
    # The inner label of a nested test is in no trace event, only in the
    # condition (no value is made under it), so the run's condition history
    # must reach the failure.
    plan = "x = 0\nif nested():\n    pass\ny = 1 + 1 + 1 + 1 + 1 + 1 + 1"
    interpreter = Interpreter(
        {**TOOLS, "nested": _nested}, requester=OWNER, mint=MINT, limits=Limits(max_steps=8)
    )
    with pytest.raises(LabeledPlanError) as err:
        interpreter.run(parse_plan(plan))
    assert (err.value.code, err.value.line) == (PlanErrorCode.STEP_LIMIT, 4)
    assert ITEM in err.value.label.sources
