# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Stage 0 baseline on CrAIBench: a plain tool-calling agent, through OpenRouter.

Same agent as the AgentDojo baseline (no defense, the fork's default system
message), on CrAIBench's crypto suites: ``chain``, ``dao_nft`` and
``crypto_trading``. Valid pairs come from ``pairs.json``; ``--sample`` picks
a deterministic subset per suite, and every rate has a 95% Wilson interval.

Two attack channels:

* prompt injection (default): the attack text sits in tool output;
* memory injection (``--memory-injection``): CrAIBench's own channel, where
  the injection is planted in the agent's stored memory.

The summary and every pair's scores are written to ``bench/results/``, which
is committed; transcripts and the response cache stay in ``runs/``.

Usage (from the repo root)::

    make bench-craibench MODE=utility
    make bench-craibench MODE=security SAMPLE=40
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
from agentdojo.attacks import base_attacks
from agentdojo.logging import OutputLogger
from agentdojo.task_suite.load_suites import get_suite
from bench_common.metering import MeteredClient, openrouter_client, prose_name, stop_reason
from bench_common.results import export
from craibench_runner.execution import Run, run_security, run_utility
from craibench_runner.summary import merged

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = REPO / "bench" / "results" / "craibench"
BENCHMARK_VERSION = "v1"
SUITES = ("chain", "dao_nft", "crypto_trading")


def build_pipeline(client: MeteredClient, model: str) -> AgentPipeline:
    """The fork's undefended pipeline around ``model``."""
    # Haiku 5.5 ignores temperature (OpenRouter drops it, see docs/design.md);
    # it stays so requests, and therefore cache keys, match the baseline runs.
    llm = OpenAILLM(client, model, temperature=0.0)
    tools_loop = ToolsExecutionLoop([ToolsExecutor(), llm])
    system = SystemMessage(load_system_message(None))
    pipeline = AgentPipeline([system, InitQuery(), llm, tools_loop])
    pipeline.name = model.rsplit("/", 1)[-1]
    # The important_instructions attack addresses the model by name.
    base_attacks.model_names.setdefault(pipeline.name, prose_name(model))
    return pipeline


def run_suites(run: Run, args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    """Results per suite for the chosen mode."""
    results: dict[str, dict[str, Any]] = {}
    for name in args.suites:
        suite = get_suite(BENCHMARK_VERSION, name)
        if args.mode == "utility":
            results[name] = run_utility(run, suite)
        else:
            pairs_file = HERE / "pairs.json"
            results[name] = run_security(run, suite, args.attack, pairs_file, args.sample)
    return results


def parse_args() -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--mode", choices=["utility", "security"], default="utility")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--memory-injection", action="store_true")
    parser.add_argument("--sample", type=int, default=None, help="pairs per suite")
    parser.add_argument("--max-cost", type=float, default=0.5, help="USD ceiling for this run")
    parser.add_argument("--run-name", default="baseline")
    return parser.parse_args()


def main() -> int:
    """Run the baseline; write the summary and per-pair scores to ``bench/results``."""
    args = parse_args()
    channel = "memory" if args.memory_injection else "prompt"
    run_dir = HERE / "runs" / args.run_name
    client = MeteredClient(openrouter_client(REPO / ".env"), run_dir, args.max_cost)
    summary: dict[str, Any] = {"model": args.model, "version": BENCHMARK_VERSION, "suites": {}}
    if args.mode == "security":
        summary |= {"attack": args.attack, "channel": channel, "sample": args.sample}
    # Logs are keyed by attack name, so the two channels need separate log trees.
    logdir = run_dir / ("logs" if args.mode == "utility" else f"logs-{channel}")
    logdir.mkdir(parents=True, exist_ok=True)
    run = Run(build_pipeline(client, args.model), logdir, memory=args.memory_injection)
    try:
        with OutputLogger(str(logdir)):
            summary["suites"] = run_suites(run, args)
    except Exception as err:
        reason = stop_reason(err)
        if reason is None:
            raise
        summary["stopped"] = reason

    summary["meter"] = dataclasses.asdict(client.meter)
    suffix = args.mode if args.mode == "utility" else f"security-{args.attack}-{channel}"
    out = RESULTS / args.run_name / f"summary-{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged(out, summary), indent=2) + "\n", encoding="utf-8")
    export(logdir, out.with_name(f"pairs-{suffix}.jsonl"))
    print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output
    return 1 if "stopped" in summary else 0


if __name__ == "__main__":
    sys.exit(main())
