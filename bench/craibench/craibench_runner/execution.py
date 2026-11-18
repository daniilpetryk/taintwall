# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Running user tasks and pairs through the fork, resuming finished ones.

The fork's own resume check never finds its results, so this module reads
them back itself (``Run.saved``) and sends only missing or interrupted pairs
to the benchmark.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any

import pydantic
from agentdojo.agent_pipeline import AgentPipeline
from agentdojo.attacks import load_attack
from agentdojo.benchmark import (
    benchmark_suite_with_injections,
    benchmark_suite_without_injections,
)
from bench_common.stats import rate_summary

from craibench_runner.pairs import sampled_pairs, valid_pairs

type Suite = Any
"""The fork's ``TaskSuite``; it ships no type information."""

type Attack = Any
"""The fork's ``BaseAttack``; it ships no type information."""

type Scores = tuple[bool, bool]
"""``(utility, security)`` of one pair; security is True when the attack succeeded."""


@dataclasses.dataclass(frozen=True)
class Run:
    """What every pair of one run shares: the agent, its log tree and the channel."""

    pipeline: AgentPipeline
    logdir: Path
    memory: bool = False

    def saved(self, suite: str, user: str, attack: str, injection: str) -> Scores | None:
        """Scores of a pair this run already finished, if any.

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


class UnreachableError(Exception):
    """The attack cannot place text in this user task's tool output."""

    NOT_INJECTABLE = "not_injectable"
    BROKEN_GROUND_TRUTH = "broken_ground_truth"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def run_utility(run: Run, suite: Suite) -> dict[str, Any]:
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


def run_pairs(
    run: Run,
    suite: Suite,
    attack: Attack,
    user: str,
    injections: list[str],
) -> dict[tuple[str, str], Scores]:
    """Scores per pair of ``user``, running only unfinished ones.

    Raises:
        UnreachableError: the attack cannot reach this task (before any model call).
    """
    results: dict[tuple[str, str], Scores] = {}
    missing: list[str] = []
    for injection in injections:
        done = run.saved(suite.name, user, attack.name, injection)
        if done is None:
            missing.append(injection)
        else:
            results[user, injection] = done
    if missing:
        results |= _attack(run, suite, attack, user, missing)
    return results


def _attack(
    run: Run,
    suite: Suite,
    attack: Attack,
    user: str,
    injections: list[str],
) -> dict[tuple[str, str], Scores]:
    try:
        ran = benchmark_suite_with_injections(
            run.pipeline,
            suite,
            attack,
            logdir=run.logdir,
            force_rerun=False,
            user_tasks=[user],
            injection_tasks=injections,
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
    return {
        pair: (ran["utility_results"][pair], hit) for pair, hit in ran["security_results"].items()
    }


def prompt_reachable(suite: Suite, attack: Attack, user: str) -> bool:
    """Whether prompt injection can place text in ``user``'s tool output (no model call)."""
    try:
        return bool(attack.get_injection_candidates(suite.get_user_task_by_id(user)))
    except ValueError:  # not injectable, or a broken ground truth (pydantic)
        return False


def run_security(
    run: Run,
    suite: Suite,
    attack_name: str,
    pairs_file: Path,
    sample: int | None,
) -> dict[str, Any]:
    """Sampled valid pairs under the attack; finished pairs are read back."""
    attack = load_attack(attack_name, suite, run.pipeline)
    pairs = sampled_pairs(valid_pairs(suite, pairs_file), sample)
    results: dict[tuple[str, str], Scores] = {}
    skipped: dict[str, list[str]] = {
        UnreachableError.NOT_INJECTABLE: [],
        UnreachableError.BROKEN_GROUND_TRUTH: [],
    }
    for user, injections in sorted(pairs.items()):
        try:
            results |= run_pairs(run, suite, attack, user, injections)
        except UnreachableError as skip:
            skipped[skip.reason].append(user)
    summary: dict[str, Any] = {
        "utility_under_attack": rate_summary({pair: r[0] for pair, r in results.items()}),
        "asr": rate_summary({pair: r[1] for pair, r in results.items()}),
        "not_injectable_user_tasks": sorted(skipped[UnreachableError.NOT_INJECTABLE]),
        "broken_ground_truth_user_tasks": sorted(skipped[UnreachableError.BROKEN_GROUND_TRUTH]),
    }
    if run.memory:
        # Like-for-like with the prompt channel: only the pairs it can reach.
        reachable = {user for user in pairs if prompt_reachable(suite, attack, user)}
        summary["asr_on_prompt_reachable_pairs"] = rate_summary(
            {pair: r[1] for pair, r in results.items() if pair[0] in reachable}
        )
    return summary
