# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Stage 0 baseline: a plain tool-calling agent on AgentDojo, through OpenRouter.

No defense: the agent is AgentDojo's standard pipeline (system message, user
query, LLM, tool loop). Its utility and attack success rate are what the
taintwall runtime is later compared against.

Every model call goes through ``bench_common.metering.MeteredClient``: a disk
cache by request hash (reruns are free), usage and cost per call in
``usage.jsonl``, and a hard ``--max-cost`` ceiling. Rates come with a 95%
Wilson interval.

Usage (from the repo root)::

    make bench-baseline ATTACK=important_instructions MAX_COST=1.0
"""

import argparse
import dataclasses
import json
import sys
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
from bench_common.metering import CostCeilingError, MeteredClient, openrouter_client
from bench_common.stats import rate_summary

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BENCHMARK_VERSION = "v1.2.2"


def build_pipeline(client: MeteredClient, model: str) -> AgentPipeline:
    """AgentDojo's undefended pipeline around ``model``."""
    llm = OpenAILLM(client, model, temperature=0.0)
    tools_loop = ToolsExecutionLoop([ToolsExecutor(), llm])
    system = SystemMessage(load_system_message(None))
    pipeline = AgentPipeline([system, InitQuery(), llm, tools_loop])
    pipeline.name = model.rsplit("/", 1)[-1]
    # The important_instructions attack addresses the model by name.
    MODEL_NAMES.setdefault(pipeline.name, "Claude")
    return pipeline


def main() -> int:
    """Run the baseline and write ``runs/<name>/summary-<mode>.json``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--suite", default="banking")
    parser.add_argument("--mode", choices=["utility", "security", "all"], default="utility")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--user-tasks", nargs="*", default=None)
    parser.add_argument("--injection-tasks", nargs="*", default=None)
    parser.add_argument("--max-cost", type=float, default=0.5, help="USD ceiling for this run")
    parser.add_argument("--run-name", default="baseline")
    args = parser.parse_args()

    run_dir = HERE / "runs" / args.run_name
    client = MeteredClient(openrouter_client(REPO / ".env"), run_dir, args.max_cost)
    pipeline = build_pipeline(client, args.model)
    suite = get_suite(BENCHMARK_VERSION, args.suite)
    logdir = run_dir / "logs"
    summary: dict[str, Any] = {
        "model": args.model,
        "suite": args.suite,
        "version": BENCHMARK_VERSION,
    }

    logdir.mkdir(parents=True, exist_ok=True)
    try:
        with OutputLogger(str(logdir)):
            run_modes(args, pipeline, suite, logdir, summary)
    except CostCeilingError as err:
        summary["stopped"] = str(err)

    summary["meter"] = dataclasses.asdict(client.meter)
    suffix = args.mode if args.mode == "utility" else f"{args.mode}-{args.attack}"
    summary_file = run_dir / f"summary-{suffix}.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output
    return 1 if "stopped" in summary else 0


def run_modes(
    args: argparse.Namespace,
    pipeline: AgentPipeline,
    suite: Any,  # noqa: ANN401 - AgentDojo's TaskSuite is generic over its environment
    logdir: Path,
    summary: dict[str, Any],
) -> None:
    """Run utility and/or security mode and add their rates to ``summary``."""
    if args.mode in {"utility", "all"}:
        results = benchmark_suite_without_injections(
            pipeline,
            suite,
            logdir=logdir,
            force_rerun=False,
            user_tasks=args.user_tasks,
            benchmark_version=BENCHMARK_VERSION,
        )
        summary["utility"] = rate_summary(results["utility_results"])
    if args.mode in {"security", "all"}:
        attack = load_attack(args.attack, suite, pipeline)
        results = benchmark_suite_with_injections(
            pipeline,
            suite,
            attack,
            logdir=logdir,
            force_rerun=False,
            user_tasks=args.user_tasks,
            injection_tasks=args.injection_tasks,
            benchmark_version=BENCHMARK_VERSION,
        )
        summary["attack"] = args.attack
        summary["utility_under_attack"] = rate_summary(results["utility_results"])
        # AgentDojo's security result is True when the injection task succeeded.
        summary["asr"] = rate_summary(results["security_results"])


if __name__ == "__main__":
    sys.exit(main())
