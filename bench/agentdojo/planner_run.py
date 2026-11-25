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

from agentdojo.attacks import load_attack
from agentdojo.task_suite.load_suites import get_suite
from bench_common.stats import rate_summary
from planner_agent import PlannerAgent, StopRunError, Watched

from taintwall.llm import BudgetedClient, CachedClient, OpenRouterClient, read_key
from taintwall.planner import PlannerModel, Replan

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = REPO / "bench" / "results" / "agentdojo"
BENCHMARK_VERSION = "v1.2.2"


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
    suite = get_suite(BENCHMARK_VERSION, args.suite)
    agent = PlannerAgent(
        Watched(budget),
        PlannerModel(args.model, args.reasoning_effort or None),
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
    write(args, run_dir, summary, done, agent.transcripts)
    return 1 if "stopped" in summary else 0


def write(
    args: argparse.Namespace,
    run_dir: Path,
    summary: dict[str, Any],
    done: Sequence[Pair],
    transcripts: Sequence[dict[str, Any]],
) -> None:
    """The summary and the pairs to ``bench/results``; the transcripts to ``runs/``."""
    suffix = args.mode if args.mode == "utility" else f"{args.mode}-{args.attack}"
    out = RESULTS / args.run_name / f"summary-{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    out.with_name(f"pairs-{suffix}.jsonl").write_text(
        "".join(json.dumps(dataclasses.asdict(p)) + "\n" for p in done), encoding="utf-8"
    )
    # Plans and answers may quote the task's data: they stay in runs/.
    (run_dir / f"transcripts-{suffix}.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in transcripts), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output


if __name__ == "__main__":
    sys.exit(main())
