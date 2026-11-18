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
from bench_common.metering import MeteredClient, openrouter_client, prose_name, stop_reason
from bench_common.stats import pairs_of, rate_summary, sample

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
    base_attacks.model_names.setdefault(pipeline.name, prose_name(model))
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
    grouped: dict[str, list[str]] = defaultdict(list)
    for pair in sample(pairs_of(pairs), size, SAMPLE_SEED):
        user, injection = pair.split(":")
        grouped[user].append(injection)
    return dict(grouped)


@dataclasses.dataclass(frozen=True)
class Run:
    """What every pair of one run shares: the agent, its log tree and the channel."""

    pipeline: AgentPipeline
    logdir: Path
    memory: bool = False

    def saved(self, suite: str, user: str, attack: str, injection: str) -> tuple[bool, bool] | None:
        """``(utility, security)`` of a pair this run already finished, if any.

        The fork writes results to ``<logdir>/<model>/<system message>/<suite>/...``
        (memory results with a ``_memory_injection`` suffix), but its own resume
        check looks under ``<logdir>/<model>/<suite>/...``, so it never finds
        them and reruns every pair. Reading them here makes reruns skip
        finished pairs.
        """
        suffix = "_memory_injection" if self.memory and attack != "none" else ""
        pattern = f"*/{suite}/{user}/{attack}/{injection}{suffix}.json"
        for path in sorted((self.logdir / str(self.pipeline.name)).glob(pattern)):
            data = json.loads(path.read_text(encoding="utf-8"))
            # The fork's logger writes messages as it goes and the scores at the
            # end: a file without them is a pair interrupted mid-task.
            if "utility" in data and "security" in data:
                return bool(data["utility"]), bool(data["security"])
        return None


def run_utility(run: Run, suite: Any) -> dict[str, Any]:  # noqa: ANN401 - untyped fork
    """Every user task without an attack; finished tasks are read back."""
    utility: dict[str, bool] = {}
    missing: list[str] = []
    for user in sorted(suite.user_tasks):
        done = run.saved(suite.name, user, "none", "none")
        if done is None:
            missing.append(user)
        else:
            utility[user] = done[0]
    if missing:
        results = benchmark_suite_without_injections(
            run.pipeline, suite, logdir=run.logdir, force_rerun=False, user_tasks=missing
        )
        utility |= {str(key): value for key, value in results["utility_results"].items()}
    return {"utility": rate_summary(utility)}


class UnreachableError(Exception):
    """The attack cannot place text in this user task's tool output."""

    NOT_INJECTABLE = "not_injectable"
    BROKEN_GROUND_TRUTH = "broken_ground_truth"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def run_pairs(
    run: Run,
    suite: Any,  # noqa: ANN401 - untyped fork
    attack: Any,  # noqa: ANN401 - untyped fork
    user: str,
    injections: list[str],
) -> dict[tuple[str, str], tuple[bool, bool]]:
    """``(utility, security)`` per pair of ``user``, running only unfinished ones.

    Raises:
        UnreachableError: the attack cannot reach this task (before any model call).
    """
    results: dict[tuple[str, str], tuple[bool, bool]] = {}
    missing: list[str] = []
    for injection in injections:
        done = run.saved(suite.name, user, attack.name, injection)
        if done is None:
            missing.append(injection)
        else:
            results[user, injection] = done
    if not missing:
        return results
    try:
        ran = benchmark_suite_with_injections(
            run.pipeline,
            suite,
            attack,
            logdir=run.logdir,
            force_rerun=False,
            user_tasks=[user],
            injection_tasks=missing,
            memory_injection=run.memory,
        )
    except pydantic.ValidationError:
        # The fork's ground truth for this task calls a tool with missing arguments.
        raise UnreachableError(UnreachableError.BROKEN_GROUND_TRUTH) from None
    except ValueError as err:
        # No tool output of this task carries attacker text.
        if "is not injectable" not in str(err):
            raise
        raise UnreachableError(UnreachableError.NOT_INJECTABLE) from None
    for pair, success in ran["security_results"].items():
        results[pair] = (ran["utility_results"][pair], success)
    return results


def run_security(run: Run, suite: Any, args: argparse.Namespace) -> dict[str, Any]:  # noqa: ANN401
    """Sampled valid pairs under the attack; finished pairs are read back."""
    attack = load_attack(args.attack, suite, run.pipeline)
    pairs = sampled_pairs(valid_pairs(suite.name, suite), args.sample)
    results: dict[tuple[str, str], tuple[bool, bool]] = {}
    skipped: dict[str, list[str]] = {
        UnreachableError.NOT_INJECTABLE: [],
        UnreachableError.BROKEN_GROUND_TRUTH: [],
    }
    for user, injections in sorted(pairs.items()):
        try:
            results |= run_pairs(run, suite, attack, user, injections)
        except UnreachableError as skip:
            skipped[skip.reason].append(user)
    # The security result is True when the injection task succeeded.
    summary: dict[str, Any] = {
        "utility_under_attack": rate_summary({p: r[0] for p, r in results.items()}),
        "asr": rate_summary({p: r[1] for p, r in results.items()}),
        "not_injectable_user_tasks": sorted(skipped[UnreachableError.NOT_INJECTABLE]),
        "broken_ground_truth_user_tasks": sorted(skipped[UnreachableError.BROKEN_GROUND_TRUTH]),
    }
    if args.memory_injection:
        # Like-for-like with the prompt channel: only the pairs it can reach.
        reachable = {user for user in pairs if prompt_reachable(suite, attack, user)}
        summary["asr_on_prompt_reachable_pairs"] = rate_summary(
            {pair: r[1] for pair, r in results.items() if pair[0] in reachable}
        )
    return summary


def prompt_reachable(suite: Any, attack: Any, user: str) -> bool:  # noqa: ANN401 - untyped fork
    """Whether prompt injection can place text in ``user``'s tool output (no model call)."""
    try:
        return bool(attack.get_injection_candidates(suite.get_user_task_by_id(user)))
    except ValueError:  # not injectable, or a broken ground truth (pydantic)
        return False


def run_suites(
    pipeline: AgentPipeline, logdir: Path, args: argparse.Namespace
) -> dict[str, dict[str, Any]]:
    """Results per suite for the chosen mode."""
    run = Run(pipeline, logdir, memory=args.memory_injection)
    results: dict[str, dict[str, Any]] = {}
    for name in args.suites:
        suite = get_suite(BENCHMARK_VERSION, name)
        if args.mode == "utility":
            results[name] = run_utility(run, suite)
        else:
            results[name] = run_security(run, suite, args)
    return results


def merged(path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    """``summary`` with the suites of an earlier run of the same mode kept.

    Suites can be run separately (for example a sampled one after the full
    ones); each run adds its suites instead of replacing the file. ``runs``
    keeps the meter of every run that called the provider, and ``meter`` is
    their total.
    """
    runs = [summary["meter"]]
    suites = summary["suites"]
    if path.exists():
        earlier = json.loads(path.read_text(encoding="utf-8"))
        runs = [*earlier.get("runs", []), *runs]
        suites = earlier.get("suites", {}) | suites
    # Only runs that called the provider; cached reruns add nothing.
    runs = [run for run in runs if run and run.get("calls")]
    total = {key: sum(run[key] for run in runs) for key in summary["meter"]}
    return summary | {"suites": suites, "runs": runs, "meter": total}


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
    except Exception as err:
        reason = stop_reason(err)
        if reason is None:
            raise
        summary["stopped"] = reason

    summary["meter"] = dataclasses.asdict(client.meter)
    suffix = args.mode if args.mode == "utility" else f"security-{args.attack}-{channel}"
    out = run_dir / f"summary-{suffix}.json"
    out.write_text(json.dumps(merged(out, summary), indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output
    return 1 if "stopped" in summary else 0


if __name__ == "__main__":
    sys.exit(main())
