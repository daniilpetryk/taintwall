# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Stage 2: AgentDojo through the taintwall planner, without policies.

Each task's prompt goes to ``taintwall.planner.Planner`` with the suite's
functions (``registry.entries``) and the reader; the planner writes a plan,
the static checker and the interpreter run it, and what it ``answer``s is the
assistant's reply AgentDojo scores. Stage 2 has no policies, so this
measures what the planner costs in utility and what the attack still reaches
through data (the reader reads injected text), not a defense:

* utility, and utility and attack success (ASR) under attack, with 95%
  Wilson intervals, as the stage 0 baseline;
* plan failure rate: tasks where no program ran to the end, by why (static,
  runtime, effects, planner), and how many attempts each took;
* with ``--replan 0 0`` CaMeL's one shot, by default Q3's retries.

Every model call goes through taintwall's own LLM layer: a disk cache by
request hash (the planner never sees data, so a task's plan is asked once
and reused for every injection), and a ceiling on calls and cost. A provider
failure or the ceiling stops the run before the pair is scored: counting it
would score a model error as a planner failure.

Usage (from the repo root)::

    make bench-planner RUN=planner-retries MAX_COST=2.0
    make bench-planner RUN=planner-one-shot REPLAN="0 0"
"""

import argparse
import dataclasses
import json
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.attacks import load_attack
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.models import MODEL_NAMES
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import ChatMessage, text_content_block_from_string
from bench_common.naming import prose_name
from bench_common.stats import rate_summary
from registry import entries

from taintwall.core.mint import Mint
from taintwall.llm import (
    BudgetedClient,
    BudgetExceededError,
    CachedClient,
    LLMClient,
    OpenRouterClient,
    ProviderError,
    StructuredReply,
    StructuredRequest,
    read_key,
)
from taintwall.planner import Outcome, Planner, PlannerModel, Replan
from taintwall.reader import reader
from taintwall.tools import Implementation, ToolSpec

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = REPO / "bench" / "results" / "agentdojo"
BENCHMARK_VERSION = "v1.2.2"
READER_CALLS = 10
"""Reader calls per request: banking tasks read a file or a few transactions."""


class StopRunError(RuntimeError):
    """The provider failed or the budget is spent: the pair must not be scored."""


class Watched:
    """An ``LLMClient`` that remembers a failure that must stop the run.

    The planner ends a request on a model error and the reader turns one
    into a failed tool call, so neither reaches the runner: this does.
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.stop: str | None = None
        self.plans: list[str] = []
        """Every program the planner sent, for the run's transcripts."""

    def structured(self, request: StructuredRequest) -> StructuredReply:
        """The inner reply; a provider failure or a spent budget is noted, then raised."""
        try:
            reply = self._inner.structured(request)
        except (ProviderError, BudgetExceededError) as err:
            self.stop = self.stop or str(err)
            raise
        if request.schema_name == "plan":
            self.plans.append(str(reply.value.get("plan")))
        return reply


class PlannerAgent(BasePipelineElement):  # type: ignore[misc]  # AgentDojo is untyped
    """The planner as an AgentDojo pipeline: the prompt in, the answer out."""

    def __init__(  # noqa: PLR0913 - the run's settings
        self,
        client: Watched,
        planner: PlannerModel,
        *,
        reader_model: str,
        replan: Replan,
        suite: Any,  # noqa: ANN401 - AgentDojo's TaskSuite is untyped
        suite_name: str,
    ) -> None:
        self._client = client
        self._planner = planner
        self._reader_model = reader_model
        self._replan = replan
        self._suite = suite
        self._suite_name = suite_name
        self._mint = Mint()
        self.name = planner.name.rsplit("/", 1)[-1] + "-taintwall"
        # The important_instructions attack addresses the model by name.
        MODEL_NAMES.setdefault(self.name, prose_name(planner.name))
        self.outcome: Outcome | None = None
        self.transcripts: list[dict[str, Any]] = []
        """What each query's planner wrote and said: kept in ``runs/``, not committed."""

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,  # noqa: ARG002 - the registry builds its own
        env: Env = EmptyEnv(),  # noqa: B008 - AgentDojo's signature
        messages: Sequence[ChatMessage] = (),
        extra_args: dict[str, Any] = {},  # noqa: B006 - AgentDojo's signature
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict[str, Any]]:
        """Plan and run ``query`` against ``env``; reply with what the plan answered."""

        def tools() -> list[tuple[ToolSpec, Implementation]]:
            found, _ = entries(self._suite, env, self._suite_name)
            ask = reader(
                self._client, self._reader_model, max_calls=READER_CALLS, reasoning_effort="low"
            )
            return [*found, ask]

        planner = Planner(self._client, self._planner, tools, replan=self._replan)
        self._client.plans.clear()
        self.outcome = planner.run(query, requester=self._mint.owner(), mint=self._mint)
        if self._client.stop is not None:
            raise StopRunError(self._client.stop)
        self.transcripts.append(
            {
                "query": query,
                "plans": list(self._client.plans),
                "errors": [[str(error) for error in a.errors] for a in self.outcome.attempts],
                "failure": self.outcome.failure,
                "answers": [answer.text for answer in self.outcome.answers],
            }
        )
        # Always an assistant reply, even an empty one: with none, AgentDojo
        # would run the task again.
        reply = "\n".join(answer.text for answer in self.outcome.answers)
        said: list[ChatMessage] = [
            {"role": "user", "content": [text_content_block_from_string(query)]},
            {
                "role": "assistant",
                "content": [text_content_block_from_string(reply)],
                "tool_calls": None,
            },
        ]
        return query, FunctionsRuntime(self._suite.tools), env, [*messages, *said], extra_args


@dataclasses.dataclass(frozen=True)
class Pair:
    """One scored task: a user task, alone or with an injection task."""

    user_task: str
    injection_task: str | None
    utility: bool
    security: bool | None
    """Whether the injection task succeeded (AgentDojo's security result)."""
    failure: str | None
    answered: bool
    attempts: tuple[str, ...]
    """Why each program before the last was not the last: phase and error code."""
    cost: float


def pair(
    agent: PlannerAgent,
    user_task: str,
    injection_task: str | None,
    *,
    utility: bool,
    security: bool | None,
) -> Pair:
    """The scores of one pair with what the planner did."""
    outcome = agent.outcome
    assert outcome is not None  # noqa: S101 - query ran
    return Pair(
        user_task,
        injection_task,
        utility,
        security,
        outcome.failure,
        outcome.answered,
        tuple(
            f"{a.phase}:{a.errors[0].code.value}" if a.errors else a.phase for a in outcome.attempts
        ),
        outcome.usage.cost,
    )


def pairs(agent: PlannerAgent, suite: Any, args: argparse.Namespace) -> Iterator[Pair]:  # noqa: ANN401
    """Every pair the mode asks for, scored by AgentDojo."""
    user_tasks = [
        suite.get_user_task_by_id(task) for task in _ids(args.user_tasks, suite.user_tasks)
    ]
    if args.mode in {"utility", "all"}:
        for task in user_tasks:
            utility, _ = suite.run_task_with_pipeline(agent, task, None, {})
            yield pair(agent, task.ID, None, utility=utility, security=None)
    if args.mode in {"security", "all"}:
        attack = load_attack(args.attack, suite, agent)
        for task in user_tasks:
            for injection_id in _ids(args.injection_tasks, suite.injection_tasks):
                injection = suite.get_injection_task_by_id(injection_id)
                injections = attack.attack(task, injection)
                utility, security = suite.run_task_with_pipeline(agent, task, injection, injections)
                yield pair(agent, task.ID, injection_id, utility=utility, security=security)


def _ids(chosen: Sequence[str] | None, every: Mapping[str, Any]) -> list[str]:
    """The chosen task ids, or every one in numeric order."""
    return list(chosen) if chosen else sorted(every, key=lambda task: int(task.rsplit("_", 1)[-1]))


def summarize(done: Sequence[Pair]) -> dict[str, Any]:
    """Rates over the scored pairs: utility, ASR and plan failures."""
    alone = [p for p in done if p.injection_task is None]
    attacked = [p for p in done if p.injection_task is not None]
    summary: dict[str, Any] = {}
    for name, group in (("utility", alone), ("under_attack", attacked)):
        if not group:
            continue
        rates = {
            "utility": rate_summary({i: p.utility for i, p in enumerate(group)}),
            "plan_failure": rate_summary({i: p.failure is not None for i, p in enumerate(group)}),
            "failures": dict(Counter(p.failure for p in group if p.failure is not None)),
            "failed_attempts": sum(1 for p in group if p.attempts),
            "answered": sum(1 for p in group if p.answered),
        }
        if name == "under_attack":
            rates["asr"] = rate_summary({i: bool(p.security) for i, p in enumerate(group)})
        summary[name] = rates
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--reader-model", default="anthropic/claude-haiku-5.5")
    parser.add_argument(
        "--replan", nargs=2, type=int, default=[2, 1], metavar=("STATIC", "RUNTIME")
    )
    parser.add_argument("--suite", default="banking")
    parser.add_argument("--mode", choices=["utility", "security", "all"], default="utility")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--user-tasks", nargs="*", default=None)
    parser.add_argument("--injection-tasks", nargs="*", default=None)
    parser.add_argument("--max-cost", type=float, default=0.5, help="USD ceiling for this run")
    parser.add_argument("--max-calls", type=int, default=5000)
    parser.add_argument("--run-name", default="planner")
    return parser.parse_args(argv)


def main() -> int:
    """Run the planner over the suite; write the summary and every pair to ``bench/results``."""
    args = parse_args()
    run_dir = HERE / "runs" / args.run_name
    budget = BudgetedClient(
        CachedClient(OpenRouterClient(read_key(REPO / ".env")), run_dir / "cache"),
        max_calls=args.max_calls,
        max_cost=args.max_cost,
    )
    client = Watched(budget)
    suite = get_suite(BENCHMARK_VERSION, args.suite)
    model = PlannerModel(args.model, args.reasoning_effort or None)
    agent = PlannerAgent(
        client,
        model,
        reader_model=args.reader_model,
        replan=Replan(*args.replan),
        suite=suite,
        suite_name=args.suite,
    )
    done: list[Pair] = []
    summary: dict[str, Any] = {
        "model": args.model,
        "reader_model": args.reader_model,
        "replan": args.replan,
        "suite": args.suite,
        "version": BENCHMARK_VERSION,
        "attack": args.attack if args.mode != "utility" else None,
    }
    try:
        done.extend(pairs(agent, suite, args))
    except StopRunError as err:
        summary["stopped"] = str(err)
    summary.update(summarize(done))
    summary["meter"] = {"calls": budget.calls, "cost": budget.cost}
    suffix = args.mode if args.mode == "utility" else f"{args.mode}-{args.attack}"
    out = RESULTS / args.run_name / f"summary-{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    out.with_name(f"pairs-{suffix}.jsonl").write_text(
        "".join(json.dumps(dataclasses.asdict(p)) + "\n" for p in done), encoding="utf-8"
    )
    # Plans and answers may quote the task's data: they stay in runs/.
    (run_dir / f"transcripts-{suffix}.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in agent.transcripts), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output
    return 1 if "stopped" in summary else 0


if __name__ == "__main__":
    sys.exit(main())
