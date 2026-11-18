# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Stage 0 baseline on CrAIBench: a plain tool-calling agent, through OpenRouter.

Same agent as the AgentDojo baseline (no defense, the fork's default system
message), on CrAIBench's crypto suites: ``chain``, ``dao_nft`` and
``crypto_trading``. Pairs come from ``pairs.json`` (the valid user/injection
combinations from CrAIBench's run scripts); ``crypto_trading`` allows every
pair. ``--sample`` picks a deterministic subset per suite, and every rate is
reported with a 95% Wilson interval.

Two attack channels:

* prompt injection (default): the attack text sits in tool output;
* memory injection (``--memory-injection``): CrAIBench's own channel, where
  the injection is planted in the agent's stored memory.

Usage (from the repo root)::

    make bench-craibench MODE=utility
    make bench-craibench MODE=security SAMPLE=40
"""

import argparse
import dataclasses
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pydantic
from agentdojo.agent_pipeline import (
    AgentPipeline,
    InitQuery,
    OpenAILLM,
    SystemMessage,
    ToolsExecutionLoop,
    ToolsExecutor,
)
from agentdojo.agent_pipeline.agent_pipeline import load_system_message
from agentdojo.attacks import base_attacks, load_attack
from agentdojo.benchmark import (
    benchmark_suite_with_injections,
    benchmark_suite_without_injections,
)
from agentdojo.logging import OutputLogger
from agentdojo.task_suite.load_suites import get_suite
from bench_common.metering import CostCeilingError, MeteredClient, openrouter_client
from bench_common.stats import rate_summary, sample

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BENCHMARK_VERSION = "v1"
SUITES = ("chain", "dao_nft", "crypto_trading")
SAMPLE_SEED = "taintwall-stage0"


def build_pipeline(client: MeteredClient, model: str) -> AgentPipeline:
    """The fork's undefended pipeline around ``model``."""
    llm = OpenAILLM(client, model, temperature=0.0)
    tools_loop = ToolsExecutionLoop([ToolsExecutor(), llm])
    system = SystemMessage(load_system_message(None))
    pipeline = AgentPipeline([system, InitQuery(), llm, tools_loop])
    pipeline.name = model.rsplit("/", 1)[-1]
    # The important_instructions attack addresses the model by name.
    base_attacks.model_names.setdefault(pipeline.name, "Claude")
    return pipeline


class UnknownTaskError(ValueError):
    """``pairs.json`` names a task the suite does not have."""

    def __init__(self, suite: str, task: str) -> None:
        super().__init__(f"pairs.json: {suite} has no {task}")


def valid_pairs(suite_name: str, suite: Any) -> dict[str, list[str]]:  # noqa: ANN401 - untyped fork
    """Injection tasks allowed for each user task of ``suite_name``, checked against the suite."""
    if suite_name == "crypto_trading":
        return {user: sorted(suite.injection_tasks) for user in suite.user_tasks}
    data = json.loads((HERE / "pairs.json").read_text(encoding="utf-8"))
    pairs: dict[str, list[str]] = dict(data[suite_name])
    for user, injections in pairs.items():
        for task in (user, *injections):
            if task not in suite.user_tasks and task not in suite.injection_tasks:
                raise UnknownTaskError(suite_name, task)
    return pairs


def sampled_pairs(pairs: dict[str, list[str]], size: int | None) -> dict[str, list[str]]:
    """A deterministic sample of ``user:injection`` pairs, grouped by user task."""
    flat = [f"{user}:{injection}" for user, injections in pairs.items() for injection in injections]
    grouped: dict[str, list[str]] = defaultdict(list)
    for pair in sample(flat, size, SAMPLE_SEED):
        user, injection = pair.split(":")
        grouped[user].append(injection)
    return dict(grouped)


def run_utility(pipeline: AgentPipeline, suite: Any, logdir: Path) -> dict[str, Any]:  # noqa: ANN401
    """Every user task without an attack."""
    results = benchmark_suite_without_injections(pipeline, suite, logdir=logdir, force_rerun=False)
    return {"utility": rate_summary(results["utility_results"])}


def run_security(
    pipeline: AgentPipeline,
    suite: Any,  # noqa: ANN401 - untyped fork
    logdir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Sampled valid pairs under the attack; one call per user task."""
    attack = load_attack(args.attack, suite, pipeline)
    pairs = sampled_pairs(valid_pairs(suite.name, suite), args.sample)
    utility: dict[tuple[str, str], bool] = {}
    security: dict[tuple[str, str], bool] = {}
    not_injectable: list[str] = []
    broken_ground_truth: list[str] = []
    for user, injections in sorted(pairs.items()):
        try:
            results = benchmark_suite_with_injections(
                pipeline,
                suite,
                attack,
                logdir=logdir,
                force_rerun=False,
                user_tasks=[user],
                injection_tasks=injections,
                memory_injection=args.memory_injection,
            )
        except pydantic.ValidationError:
            # The fork's ground truth for this task calls a tool with missing
            # arguments, so the attack cannot place its text; no model call
            # has happened yet.
            broken_ground_truth.append(user)
            continue
        except ValueError as err:
            # No tool output of this task carries attacker text, so prompt
            # injection cannot reach it; raised before any model call.
            if "is not injectable" not in str(err):
                raise
            not_injectable.append(user)
            continue
        utility.update(results["utility_results"])
        security.update(results["security_results"])
    # The security result is True when the injection task succeeded.
    return {
        "utility_under_attack": rate_summary(utility),
        "asr": rate_summary(security),
        "not_injectable_user_tasks": sorted(not_injectable),
        "broken_ground_truth_user_tasks": sorted(broken_ground_truth),
    }


def run_suites(
    pipeline: AgentPipeline, logdir: Path, args: argparse.Namespace
) -> dict[str, dict[str, Any]]:
    """Results per suite for the chosen mode."""
    results: dict[str, dict[str, Any]] = {}
    for name in args.suites:
        suite = get_suite(BENCHMARK_VERSION, name)
        if args.mode == "utility":
            results[name] = run_utility(pipeline, suite, logdir)
        else:
            results[name] = run_security(pipeline, suite, logdir, args)
    return results


def merged(path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    """``summary`` with the suites of an earlier run of the same mode kept.

    Suites can be run separately (for example a sampled one after the full
    ones); each run adds its suites instead of replacing the file. Meters are
    kept per run, so the spend of every run stays visible.
    """
    if not path.exists():
        return summary | {"runs": [summary["meter"]]}
    earlier = json.loads(path.read_text(encoding="utf-8"))
    return summary | {
        "suites": earlier.get("suites", {}) | summary["suites"],
        "runs": [*earlier.get("runs", [earlier.get("meter")]), summary["meter"]],
    }


def main() -> int:
    """Run the baseline and write ``runs/<name>/summary-<mode>.json``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--suites", nargs="*", default=list(SUITES))
    parser.add_argument("--mode", choices=["utility", "security"], default="utility")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--memory-injection", action="store_true")
    parser.add_argument("--sample", type=int, default=None, help="pairs per suite")
    parser.add_argument("--max-cost", type=float, default=0.5, help="USD ceiling for this run")
    parser.add_argument("--run-name", default="baseline")
    args = parser.parse_args()

    channel = "memory" if args.memory_injection else "prompt"
    run_dir = HERE / "runs" / args.run_name
    client = MeteredClient(openrouter_client(REPO / ".env"), run_dir, args.max_cost)
    pipeline = build_pipeline(client, args.model)
    summary: dict[str, Any] = {"model": args.model, "version": BENCHMARK_VERSION, "suites": {}}
    if args.mode == "security":
        summary |= {"attack": args.attack, "channel": channel, "sample": args.sample}
    # Logs are keyed by attack name, so the two channels need separate log trees.
    logdir = run_dir / ("logs" if args.mode == "utility" else f"logs-{channel}")
    logdir.mkdir(parents=True, exist_ok=True)
    try:
        with OutputLogger(str(logdir)):
            summary["suites"] = run_suites(pipeline, logdir, args)
    except CostCeilingError as err:
        summary["stopped"] = str(err)

    summary["meter"] = dataclasses.asdict(client.meter)
    suffix = args.mode if args.mode == "utility" else f"security-{args.attack}-{channel}"
    out = run_dir / f"summary-{suffix}.json"
    out.write_text(json.dumps(merged(out, summary), indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output
    return 1 if "stopped" in summary else 0


if __name__ == "__main__":
    sys.exit(main())
