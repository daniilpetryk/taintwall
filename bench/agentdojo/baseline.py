# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Stage 0 baseline: a plain tool-calling agent on AgentDojo, through OpenRouter.

No defense: the agent is AgentDojo's standard pipeline (system message, user
query, LLM, tool loop). Its utility and attack success rate are what the
taintwall runtime is later compared against.

Every model call goes through ``bench_common.metering.MeteredClient``: a disk
cache by request hash (reruns are free), usage and cost per call in
``usage.jsonl``, and a hard ``--max-cost`` ceiling. Rates come with a 95%
Wilson interval. With ``--draws N`` the suite runs ``N`` times, each draw
with its own cache entries and logs; rates are over every draw's pairs, and
``spread`` gives each one's value per draw. The summary and every pair's scores are written to
``bench/results/`` (committed); transcripts and the cache stay in ``runs/``.

Usage (from the repo root)::

    make bench-baseline ATTACK=important_instructions MAX_COST=1.0
    make bench-baseline DRAWS=5 RUN=banking-haiku-5.5-draws CACHE=runs/banking-haiku-5.5/cache
"""

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agentdojo.agent_pipeline import (
    AgentPipeline,
    InitQuery,
    OpenAILLM,
    SystemMessage,
    ToolsExecutionLoop,
    ToolsExecutor,
)
from agentdojo.agent_pipeline.agent_pipeline import load_system_message
from agentdojo.attacks import load_attack
from agentdojo.benchmark import (
    benchmark_suite_with_injections,
    benchmark_suite_without_injections,
)
from agentdojo.logging import OutputLogger
from agentdojo.models import MODEL_NAMES
from agentdojo.task_suite.load_suites import get_suite
from bench_common.metering import MeteredClient, openrouter_client
from bench_common.naming import prose_name
from bench_common.results import export
from bench_common.stats import rate_summary
from bench_common.stopping import stop_reason
from draws import BASELINE_RATES, spread

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = REPO / "bench" / "results" / "agentdojo"
BENCHMARK_VERSION = "v1.2.2"


def build_pipeline(client: MeteredClient, model: str) -> AgentPipeline:
    """AgentDojo's undefended pipeline around ``model``."""
    # Haiku 5.5 ignores temperature (OpenRouter drops it, see docs/design.md);
    # it stays so requests, and therefore cache keys, match the baseline runs.
    llm = OpenAILLM(client, model, temperature=0.0)
    tools_loop = ToolsExecutionLoop([ToolsExecutor(), llm])
    system = SystemMessage(load_system_message(None))
    pipeline = AgentPipeline([system, InitQuery(), llm, tools_loop])
    pipeline.name = model.rsplit("/", 1)[-1]
    # The important_instructions attack addresses the model by name.
    MODEL_NAMES.setdefault(pipeline.name, prose_name(model))
    return pipeline


def parse_args() -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--suite", default="banking")
    parser.add_argument("--mode", choices=["utility", "security", "all"], default="utility")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--user-tasks", nargs="*", default=None)
    parser.add_argument("--injection-tasks", nargs="*", default=None)
    parser.add_argument("--max-cost", type=float, default=0.5, help="USD ceiling for this run")
    parser.add_argument(
        "--draws", type=int, default=1, help="run the suite this many times, each a new draw"
    )
    parser.add_argument(
        "--cache", type=Path, default=None, help="response cache (default: the run's own)"
    )
    parser.add_argument("--run-name", default="baseline")
    return parser.parse_args()


Results = dict[str, dict[Any, bool]]
"""One draw's results by rate name: ``utility``, ``utility_under_attack``, ``asr``."""


def main() -> int:
    """Run the baseline; write the summary and per-pair scores to ``bench/results``."""
    args = parse_args()
    run_dir = HERE / "runs" / args.run_name
    client = MeteredClient(openrouter_client(REPO / ".env"), run_dir, args.max_cost, args.cache)
    pipeline = build_pipeline(client, args.model)
    suite = get_suite(BENCHMARK_VERSION, args.suite)
    summary: dict[str, Any] = {
        "model": args.model,
        "suite": args.suite,
        "version": BENCHMARK_VERSION,
        "draws": args.draws,
    }
    if args.mode != "utility":
        summary["attack"] = args.attack
    per_draw: list[Results] = []
    try:
        for draw in range(args.draws):
            client.draw = draw
            per_draw.append(run_modes(args, pipeline, suite, logs(run_dir, draw)))
    except Exception as err:
        reason = stop_reason(err)
        if reason is None:
            raise
        summary["stopped"] = reason

    summary |= rates(per_draw)
    if args.draws > 1:
        summary["spread"] = spread([rates([draw]) for draw in per_draw], BASELINE_RATES)
    summary["meter"] = dataclasses.asdict(client.meter)
    suffix = args.mode if args.mode == "utility" else f"{args.mode}-{args.attack}"
    out = RESULTS / args.run_name / f"summary-{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    # One log tree per draw holds every attack of this run; rows carry the attack name.
    for draw in range(args.draws):
        export(logs(run_dir, draw), out.with_name(pairs_file(draw)))
    print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output
    return 1 if "stopped" in summary else 0


def logs(run_dir: Path, draw: int) -> Path:
    """Where AgentDojo logs a draw: draw 0 where runs before draws logged."""
    return run_dir / ("logs" if draw == 0 else f"logs-draw-{draw}")


def pairs_file(draw: int) -> str:
    """The committed per-pair scores of a draw."""
    return "pairs.jsonl" if draw == 0 else f"pairs-draw-{draw}.jsonl"


def rates(per_draw: Sequence[Results]) -> dict[str, Any]:
    """Each rate over every pair of these draws, with its interval."""
    names = dict.fromkeys(name for draw in per_draw for name in draw)
    return {
        name: rate_summary(
            {
                (k, pair): ok
                for k, draw in enumerate(per_draw)
                for pair, ok in draw.get(name, {}).items()
            }
        )
        for name in names
    }


def run_modes(
    args: argparse.Namespace,
    pipeline: AgentPipeline,
    suite: Any,  # noqa: ANN401 - AgentDojo's TaskSuite is generic over its environment
    logdir: Path,
) -> Results:
    """One draw of utility and/or security mode: its results by rate name."""
    results: Results = {}
    logdir.mkdir(parents=True, exist_ok=True)
    with OutputLogger(str(logdir)):
        if args.mode in {"utility", "all"}:
            clean = benchmark_suite_without_injections(
                pipeline,
                suite,
                logdir=logdir,
                force_rerun=False,
                user_tasks=args.user_tasks,
                benchmark_version=BENCHMARK_VERSION,
            )
            results["utility"] = clean["utility_results"]
        if args.mode in {"security", "all"}:
            attack = load_attack(args.attack, suite, pipeline)
            attacked = benchmark_suite_with_injections(
                pipeline,
                suite,
                attack,
                logdir=logdir,
                force_rerun=False,
                user_tasks=args.user_tasks,
                injection_tasks=args.injection_tasks,
                benchmark_version=BENCHMARK_VERSION,
            )
            results["utility_under_attack"] = attacked["utility_results"]
            # AgentDojo's security result is True when the injection task succeeded.
            results["asr"] = attacked["security_results"]
    return results


if __name__ == "__main__":
    sys.exit(main())
