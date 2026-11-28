# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The planning loop: ask for a plan, check it, run it, and retry within bounds (Q3).

``Planner.run(request, ...)`` asks the P-LLM for a program, parses it and
runs the static checker. A program that cannot run goes back to the planner
with its typed errors, at most ``Replan.static_retries`` times; this loop
reads no data, so it is safe. A program that passes runs; a failure while
running goes back at most ``Replan.runtime_retries`` times, and never once
anything with an effect has been called (answering the user included), since
the plan would run again from the start. ``Replan(0, 0)`` is CaMeL's one shot.
A call a policy refuses ends the run: a retry would let the planner look for
another way to do what the policy forbids.

The planner sees only typed errors (a code, a line, a name from its own
program); a runtime error depends on data, so each one is kept with its label
in the outcome, which is where a policy can decide later whether it may be
shown. A planner reply cut at its budget, or any other model error, ends the
run: the budget is the model's (``PlannerModel``), not the loop's.
"""

import dataclasses
import textwrap
from collections.abc import Callable, Iterable, Sequence
from typing import Unpack

from taintwall.core.errors import PlanError
from taintwall.core.interpreter import (
    CallHook,
    InterpreterOptions,
    LabeledPlanError,
    PendingCall,
    RunResult,
)
from taintwall.core.plan import Plan, parse_plan
from taintwall.llm import LLMClient, LLMError, StructuredReply, StructuredRequest, Usage
from taintwall.planner import prompt
from taintwall.planner.answer import NAME as ANSWER
from taintwall.planner.answer import ReservedToolError, answer_tool
from taintwall.planner.outcome import Answer, Attempt, Failure, Outcome, Phase
from taintwall.planner.prompt import READER_TOOL as READER
from taintwall.tools import CallLog, Implementation, Registry, ToolSpec


@dataclasses.dataclass(frozen=True, slots=True)
class PlannerModel:
    """The P-LLM and what it may spend per reply."""

    name: str
    reasoning_effort: str | None
    """``"medium"`` for a reasoning model (the P-LLM's effort in design.md);
    ``None`` for one that takes no such parameter, which would refuse it."""
    max_tokens: int = 8000
    """Reasoning included: the planner study saw 2000 cut 15 of 160 replies."""


@dataclasses.dataclass(frozen=True, slots=True)
class Replan:
    """How often a plan may be retried (Q3); ``Replan(0, 0)`` is one shot."""

    static_retries: int = 2
    runtime_retries: int = 1


class Planner:
    """Plans and runs requests with these tools, ``answer`` added.

    Raises (from ``run``):
        ReservedToolError: a tool is named ``answer``, the planner's own.
        DuplicateToolError: two tools share a name.
    """

    def __init__(
        self,
        client: LLMClient,
        model: PlannerModel,
        tools: Callable[[], Iterable[tuple[ToolSpec, Implementation]]],
        *,
        replan: Replan = Replan(),  # noqa: B008 - a frozen dataclass
        policies: Callable[[], Iterable[CallHook]] = tuple,
    ) -> None:
        self._client = client
        self._model = model
        self._tools = tools
        self._replan = replan
        self._policies = policies

    def run(self, request: str, **options: Unpack[InterpreterOptions]) -> Outcome:
        """Plan ``request``, check it, run it, and retry within the bounds.

        ``tools`` and ``policies`` are called once per run: a tool or a policy
        may hold state for one request (the reader's call budget, a benchmark
        task's environment, what a request has spent).
        """
        run = _Run(request, tuple(self._tools()))
        policies = tuple(_Watched(policy, run) for policy in self._policies())
        while True:
            source = self._ask(run)
            if source is None:
                return run.end("planner")
            plan = _check(source, run.registry)
            if not isinstance(plan, Plan):
                stop = self._static(run, request, source, *plan)
            else:
                interpreter, run.log = run.registry.interpreter(
                    policies=policies, log=run.log, **options
                )
                try:
                    return run.end(None, interpreter.run(plan))
                except LabeledPlanError as err:
                    stop = self._stopped(run, request, source, err)
            if stop is not None:
                return run.end(stop)

    def _static(
        self, run: "_Run", request: str, source: str, phase: Phase, errors: tuple[PlanError, ...]
    ) -> Failure | None:
        """Note a program that cannot run: ``"static"`` if the retries are spent."""
        run.attempts.append(Attempt(source, phase, errors))
        if run.static >= self._replan.static_retries:
            return "static"
        run.static += 1
        run.user = prompt.static_retry(request, source, errors, run.earlier())
        return None

    def _stopped(
        self, run: "_Run", request: str, source: str, err: LabeledPlanError
    ) -> Failure | None:
        """Note a program that stopped while running: why the run ends, if it does."""
        # A fresh error: the raised one holds the interpreter's frames, and
        # with them the values the plan read, in its traceback.
        clean = LabeledPlanError(err, err.label)
        run.attempts.append(Attempt(source, "run", (clean,), err.label))
        if run.refused:
            return "policy"
        if run.log is not None and run.log.effects_happened:
            return "effects"
        if run.runtime >= self._replan.runtime_retries:
            return "runtime"
        run.runtime += 1
        run.user = prompt.runtime_retry(request, source, clean, run.earlier())
        return None

    def _ask(self, run: "_Run") -> str | None:
        """The planner's program, or ``None`` if the model gave none."""
        request = StructuredRequest(
            model=self._model.name,
            system=run.system,
            user=run.user,
            schema_name=prompt.PLAN_SCHEMA_NAME,
            schema=prompt.PLAN_SCHEMA,
            max_tokens=self._model.max_tokens,
            reasoning_effort=self._model.reasoning_effort,
        )
        reply = _ask(self._client, request)
        run.add(reply.usage)
        if isinstance(reply, LLMError):
            run.error = reply
            return None
        return _strip_fence(str(reply.value[prompt.PLAN_FIELD]))


def _ask(client: LLMClient, request: StructuredRequest) -> StructuredReply | LLMError:
    try:
        return client.structured(request)
    except LLMError as err:
        return err


def _check(source: str, registry: Registry) -> Plan | tuple[Phase, tuple[PlanError, ...]]:
    """The plan if it may run, else where it was stopped and the errors that stop it."""
    try:
        plan = parse_plan(source)
    except PlanError as err:
        return "parse", (err,)
    check = registry.check(plan)
    return plan if check.ok else ("check", check.errors)


def _strip_fence(source: str) -> str:
    """The program, without a Markdown code fence or an indent a model may add."""
    lines = textwrap.dedent(source.strip("\n")).strip().splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":  # noqa: PLR2004 - two fences
        lines = lines[1:-1]
    return textwrap.dedent("\n".join(lines)).strip()


class _Watched:
    """A policy whose refusal the run notes, so that the run ends there."""

    def __init__(self, policy: CallHook, run: "_Run") -> None:
        self._policy = policy
        self._run = run

    def before(self, call: PendingCall) -> None:
        """The policy's check; a refusal or a crash (both stop the call) is noted."""
        try:
            self._policy.before(call)
        except BaseException:
            self._run.refused = True
            raise


class _Run:
    """One request: its tools, what it has tried and spent, the call log of its attempts."""

    def __init__(self, request: str, tools: Sequence[tuple[ToolSpec, Implementation]]) -> None:
        if any(spec.name == ANSWER for spec, _ in tools):
            raise ReservedToolError(ANSWER)
        spec, run_answer, self.said = answer_tool()
        self.registry = Registry([*tools, (spec, run_answer)])
        self.system = prompt.system(
            self.registry.describe(), has_reader=READER in self.registry.specs
        )
        self.error: LLMError | None = None
        self.user = prompt.request(request)
        self.log: CallLog | None = None
        self.attempts: list[Attempt] = []
        # Only ever tested for truth: None would read the same.
        self.refused = False  # pragma: no mutate
        self.static = 0
        self.runtime = 0
        self.usage = Usage()

    def earlier(self) -> list[tuple[PlanError, ...]]:
        """The errors of every attempt before the last."""
        return [attempt.errors for attempt in self.attempts[:-1]]

    def add(self, usage: Usage) -> None:
        self.usage = Usage(
            self.usage.prompt_tokens + usage.prompt_tokens,
            self.usage.completion_tokens + usage.completion_tokens,
            self.usage.cached_tokens + usage.cached_tokens,
            self.usage.cost + usage.cost,
        )

    def end(self, failure: Failure | None, result: RunResult | None = None) -> Outcome:
        log = self.log
        calls = [] if log is None else [record for record in log.records if record.name == ANSWER]
        answers = tuple(
            Answer(text, label)
            # One text per recorded call: the log records a call just before it runs.
            for text, record in zip(self.said, calls, strict=False)  # pragma: no mutate
            if (label := record.label_of("text")) is not None
        )
        return Outcome(
            attempts=tuple(self.attempts),
            answers=answers,
            result=result,
            failure=failure,
            planner_error=self.error,
            effects_happened=log is not None and log.effects_happened,
            calls=() if log is None else log.records,
            usage=self.usage,
        )
