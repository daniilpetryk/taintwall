# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The planner as an AgentDojo pipeline, with a scripted model: no calls are made."""

import argparse

import pytest
from agentdojo.task_suite.load_suites import get_suite
from planner_agent import PlannerAgent, StopRunError, Watched
from planner_run import Pair, pairs, parse_args, settings, summarize

from taintwall.llm import ProviderError, StructuredReply, StructuredRequest, TruncatedError, Usage
from taintwall.planner import PlannerModel, Replan
from taintwall.reader import ENOUGH

SUITE = get_suite("v1.2.2", "banking")
MODEL = PlannerModel("m/planner", "low")


class Scripted:
    """Answers the planner with ``plan`` and the reader with ``fields``."""

    def __init__(self, plan: str, fields: dict[str, object] | None = None) -> None:
        self.plan = plan
        self.fields = fields or {}
        self.requests: list[StructuredRequest] = []

    def structured(self, request: StructuredRequest) -> StructuredReply:
        self.requests.append(request)
        if request.schema_name == "plan":
            return StructuredReply({"plan": self.plan}, Usage(10, 5, 0, 0.01))
        return StructuredReply({**self.fields, ENOUGH: True}, Usage(5, 2, 0, 0.001))


class Failing:
    def __init__(self, status: int | None) -> None:
        self.status = status

    def structured(self, request: StructuredRequest) -> StructuredReply:
        raise ProviderError(request.model, self.status)


def agent(client: object, replan: Replan | None = None) -> PlannerAgent:
    watched = Watched(client)  # type: ignore[arg-type]
    return PlannerAgent(
        watched,
        MODEL,
        reader_model="m/reader",
        reader_effort="low",
        reader_calls=10,
        replan=replan or Replan(),
        suite=SUITE,
        suite_name="banking",
    )


def args(*argv: str) -> argparse.Namespace:
    return parse_args(["--user-tasks", "user_task_0", *argv])


def test_the_answer_is_the_reply_agentdojo_scores() -> None:
    planner = agent(Scripted("b = get_balance()\nanswer(f'Your balance is {b:.2f}')"))
    env = SUITE.load_and_inject_default_environment({})
    _, _, _, messages, _ = planner.query("Balance?", None, env)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    reply = messages[-1]["content"]
    assert reply is not None
    assert reply[0]["content"] == "Your balance is 1810.00"
    assert planner.outcome is not None
    assert planner.outcome.answered
    (transcript,) = planner.transcripts
    assert transcript["plans"] == ["b = get_balance()\nanswer(f'Your balance is {b:.2f}')"]
    assert (transcript["errors"], transcript["answers"]) == ([], ["Your balance is 1810.00"])


def test_a_plan_that_never_answers_still_replies() -> None:
    planner = agent(Scripted("x = y"), Replan(0, 0))
    env = SUITE.load_and_inject_default_environment({})
    _, _, _, messages, _ = planner.query("Balance?", None, env)
    reply = messages[-1]["content"]
    assert reply is not None
    assert reply[0]["content"] == ""
    assert planner.outcome is not None
    assert planner.outcome.failure == "static"


def test_pairs_are_scored_with_what_the_planner_did() -> None:
    planner = agent(Scripted("b = get_balance()\nanswer(str(b))"))
    (alone,) = pairs(planner, SUITE, args("--mode", "utility"))
    assert (alone.user_task, alone.injection_task, alone.security) == ("user_task_0", None, None)
    assert (alone.failure, alone.answered, alone.attempts) == (None, True, ())
    assert (alone.calls, alone.cost) == (1, 0.01)
    assert planner.refused == {}
    attacked = list(
        pairs(planner, SUITE, args("--mode", "security", "--injection-tasks", "injection_task_0"))
    )
    assert [(p.injection_task, p.security) for p in attacked] == [("injection_task_0", False)]


def test_the_same_plan_serves_every_injection() -> None:
    model = Scripted("b = get_balance()\nanswer(str(b))")
    planner = agent(model)
    chosen = ("--injection-tasks", "injection_task_0", "injection_task_1")
    list(pairs(planner, SUITE, args("--mode", "security", *chosen)))
    plans = {(r.system, r.user) for r in model.requests if r.schema_name == "plan"}
    assert len(plans) == 1  # the planner never sees data: one request, cached for the rest


def test_a_provider_failure_stops_the_run_unscored() -> None:
    planner = agent(Failing(503))
    with pytest.raises(StopRunError, match="provider error"):
        list(pairs(planner, SUITE, args("--mode", "utility")))


def test_a_reader_failure_from_the_provider_also_stops_the_run() -> None:
    class ReaderFails(Scripted):
        def structured(self, request: StructuredRequest) -> StructuredReply:
            if request.schema_name == "plan":
                return super().structured(request)
            raise ProviderError(request.model, None)

    plan = (
        'f = read_file("bill-december-2023.txt")\n'
        'x = query_reader("Rent?", f, {"a": "float"})\n'
        "answer(str(x.a))"
    )
    planner = agent(ReaderFails(plan))
    with pytest.raises(StopRunError, match="unreachable"):
        list(pairs(planner, SUITE, args("--mode", "utility")))


def scored(  # noqa: PLR0913 - a pair's fields
    user: str,
    injection: str | None,
    *,
    utility: bool,
    security: bool | None = None,
    failure: str | None = None,
    attempts: tuple[str, ...] = (),
) -> Pair:
    answered = failure != "static"
    return Pair(user, injection, utility, security, failure, answered, attempts, 2, 0.1)


def test_the_summary_counts_utility_asr_and_plan_failures() -> None:
    done = [
        scored("u0", None, utility=True),
        scored("u1", None, utility=False, failure="static", attempts=("check:unknown_tool",) * 3),
        scored("u0", "i0", utility=True, security=False, attempts=("run:tool_failed",)),
        scored("u0", "i1", utility=False, security=True, failure="effects"),
    ]
    summary = summarize(done)
    assert summary["utility"]["utility"]["rate"] == 0.5
    assert summary["utility"]["plan_failure"]["successes"] == 1
    assert summary["utility"]["failures"] == {"static": 1}
    attacked = summary["under_attack"]
    assert (attacked["asr"]["successes"], attacked["asr"]["total"]) == (1, 2)
    assert (attacked["failed_attempts"], attacked["answered"]) == (1, 2)
    assert summarize([]) == {}


def test_a_request_counts_its_reader_calls_and_their_cost() -> None:
    plan = (
        'f = read_file("bill-december-2023.txt")\n'
        'x = query_reader("Amount?", f, {"a": "float"})\n'
        "answer(str(x.a))"
    )
    planner = agent(Scripted(plan, {"a": 98.7}))
    (done,) = pairs(planner, SUITE, args("--mode", "utility"))
    assert done.calls == 2  # the planner and the reader
    assert done.cost == pytest.approx(0.011)
    assert planner.transcripts[-1]["answers"] == ["98.7"]


def test_a_failed_reply_still_counts_what_it_was_billed() -> None:
    class Cut(Scripted):
        def structured(self, request: StructuredRequest) -> StructuredReply:
            self.requests.append(request)
            raise TruncatedError(request.model, Usage(1, 8000, 0, 0.3))

    watched = Watched(Cut("x"))
    with pytest.raises(TruncatedError):
        watched.structured(StructuredRequest("m", "s", "u", "plan", {"type": "object"}, 10))
    assert (watched.calls, watched.cost, watched.stop) == (1, 0.3, None)
    watched.reset()
    assert (watched.calls, watched.cost, watched.plans) == (0, 0.0, [])


def test_the_settings_name_everything_that_changes_the_numbers() -> None:
    chosen = settings(args("--mode", "utility", "--reasoning-effort", "", "--replan", "0", "0"))
    assert chosen["reasoning_effort"] is None
    assert (chosen["reader_reasoning_effort"], chosen["reader_calls"]) == ("low", 10)
    assert (chosen["replan"], chosen["attack"]) == ([0, 0], None)
    assert settings(args("--mode", "all"))["attack"] == "important_instructions"
