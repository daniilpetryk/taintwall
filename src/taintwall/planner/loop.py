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

The planner sees only typed errors (a code, a line, a name from its own
program); a runtime error depends on data, so each one is kept with its label
in the outcome, which is where a policy can decide later whether it may be
shown. A planner reply cut at its budget, or any other model error, ends the
run: the budget is the model's (``PlannerModel``), not the loop's.
"""

import dataclasses
import textwrap
from collections.abc import Callable, Iterable, Sequence
from typing import Final, Literal, Unpack

from taintwall.core.errors import PlanError
from taintwall.core.interpreter import CallHook, InterpreterOptions, LabeledPlanError, RunResult
from taintwall.core.labels import Label
from taintwall.core.plan import Plan, parse_plan
from taintwall.llm import LLMClient, LLMError, StructuredReply, StructuredRequest, Usage
from taintwall.planner import prompt
from taintwall.planner.answer import NAME as ANSWER
from taintwall.planner.answer import ReservedToolError, answer_tool
from taintwall.planner.prompt import READER_TOOL as READER
from taintwall.tools import CallLog, CallRecord, Implementation, Registry, ToolSpec

type Phase = Literal["parse", "check", "run"]
type Failure = Literal["static", "runtime", "effects", "planner"]


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


@dataclasses.dataclass(frozen=True, slots=True)
class Attempt:
    """One program the planner wrote, and why it was not the last."""

    source: str
    phase: Phase
    errors: tuple[PlanError, ...] = ()
    label: Label | None = None
    """For a runtime error: what it depends on (data the planner never saw)."""


@dataclasses.dataclass(frozen=True, slots=True)
class Answer:
    """What the user was told, and the label of what it was built from."""

    text: str
    label: Label


@dataclasses.dataclass(frozen=True, slots=True)
class Outcome:
    """How a request ended."""

    attempts: tuple[Attempt, ...]
    answers: tuple[Answer, ...]
    result: RunResult | None
    """The last run's variables and trace, if a program ran to the end."""
    failure: Failure | None
    """``None`` when a program ran to the end."""
    planner_error: LLMError | None
    """Why the planner's model gave no program, when ``failure`` is ``"planner"``."""
    effects_happened: bool
    calls: tuple[CallRecord, ...]
    """Every tool call of every attempt, in order: the shared call log's records."""
    usage: Usage

    @property
    def completed(self) -> bool:
        """Whether a program ran to the end, answering or not."""
        return self.failure is None

    @property
    def answered(self) -> bool:
        """Whether a program ran to the end and told the user something."""
        return self.completed and bool(self.answers)


PLAN_FIELD: Final = "plan"


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
        policies: Sequence[CallHook] = (),
    ) -> None:
        self._client = client
        self._model = model
        self._tools = tools
        self._replan = replan
        self._policies = tuple(policies)

    def run(self, request: str, **options: Unpack[InterpreterOptions]) -> Outcome:
        """Plan ``request``, check it, run it, and retry within the bounds.

        ``tools`` is called once per run: a tool may hold state for one request
        (the reader's call budget, a benchmark task's environment).
        """
        run = _Run(request, tuple(self._tools()))
        while True:
            source = self._ask(run)
            if source is None:
                return run.end("planner")
            plan = _check(source, run.registry)
            if not isinstance(plan, Plan):
                phase, errors = plan
                run.attempts.append(Attempt(source, phase, errors))
                if run.static >= self._replan.static_retries:
                    return run.end("static")
                run.static += 1
                run.user = prompt.static_retry(request, source, errors, run.earlier())
                continue
            interpreter, run.log = run.registry.interpreter(
                policies=self._policies, log=run.log, **options
            )
            try:
                result = interpreter.run(plan)
            except LabeledPlanError as err:
                # A fresh error: the raised one holds the interpreter's frames,
                # and with them the values the plan read, in its traceback.
                clean = LabeledPlanError(err, err.label)
                run.attempts.append(Attempt(source, "run", (clean,), err.label))
                if run.log.effects_happened:
                    return run.end("effects")
                if run.runtime >= self._replan.runtime_retries:
                    return run.end("runtime")
                run.runtime += 1
                run.user = prompt.runtime_retry(request, source, clean, run.earlier())
                continue
            return run.end(None, result)

    def _ask(self, run: "_Run") -> str | None:
        """The planner's program, or ``None`` if the model gave none."""
        request = StructuredRequest(
            model=self._model.name,
            system=run.system,
            user=run.user,
            schema_name="plan",
            schema=prompt.PLAN_SCHEMA,
            max_tokens=self._model.max_tokens,
            reasoning_effort=self._model.reasoning_effort,
        )
        reply = _ask(self._client, request)
        run.add(reply.usage)
        if isinstance(reply, LLMError):
            run.error = reply
            return None
        return _strip_fence(str(reply.value[PLAN_FIELD]))


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
