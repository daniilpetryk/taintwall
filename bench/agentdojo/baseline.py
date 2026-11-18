# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Stage 0 baseline: a plain tool-calling agent on AgentDojo, through OpenRouter.

No defense: the agent is AgentDojo's standard pipeline (system message, user
query, LLM, tool loop). Its utility and attack success rate are what the
taintwall runtime is later compared against.

Every model call goes through ``MeteredClient``:

* responses are cached on disk by request hash, so a rerun costs nothing and
  the baseline is computed once (budget rule);
* usage and cost of every uncached call are appended to ``usage.jsonl``;
* a hard ceiling (``--max-cost``) stops the run before it spends more.

Usage (from bench/agentdojo)::

    uv run python baseline.py --mode utility --user-tasks user_task_0
    uv run python baseline.py --mode all --max-cost 2.5
"""

import argparse
import dataclasses
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import openai
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
from dotenv import dotenv_values
from openai import NOT_GIVEN
from openai.types.chat import ChatCompletion

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BENCHMARK_VERSION = "v1.2.2"
OPENROUTER = "https://openrouter.ai/api/v1"


class CostCeilingError(RuntimeError):
    """The run would spend more than ``--max-cost``."""

    def __init__(self, spent: float, ceiling: float) -> None:
        super().__init__(f"spent ${spent:.4f}, ceiling ${ceiling:.2f}")


@dataclasses.dataclass
class Meter:
    """Running totals of uncached calls."""

    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0


class MeteredClient:
    """Stands in for ``openai.OpenAI`` where AgentDojo calls ``chat.completions.create``."""

    def __init__(self, client: openai.OpenAI, run_dir: Path, max_cost: float) -> None:
        self._client = client
        self._cache = run_dir / "cache"
        self._cache.mkdir(parents=True, exist_ok=True)
        self._usage_log = run_dir / "usage.jsonl"
        self._max_cost = max_cost
        self.meter = Meter()

    @property
    def chat(self) -> "MeteredClient":
        """``client.chat``, as in the OpenAI SDK."""
        return self

    @property
    def completions(self) -> "MeteredClient":
        """``client.chat.completions``, as in the OpenAI SDK."""
        return self

    def create(self, **kwargs: Any) -> ChatCompletion:  # noqa: ANN401 - mirrors the OpenAI SDK
        """A cached or metered ``chat.completions.create``."""
        request = {key: value for key, value in kwargs.items() if value is not NOT_GIVEN}
        key = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
        cached = self._cache / f"{key}.json"
        if cached.exists():
            self.meter.cache_hits += 1
            return ChatCompletion.model_validate_json(cached.read_text(encoding="utf-8"))
        if self.meter.cost >= self._max_cost:
            raise CostCeilingError(self.meter.cost, self._max_cost)
        completion: ChatCompletion = self._client.chat.completions.create(
            **request, extra_body={"usage": {"include": True}}
        )
        cached.write_text(completion.model_dump_json(), encoding="utf-8")
        self._record(completion)
        return completion

    def _record(self, completion: ChatCompletion) -> None:
        usage = completion.usage
        details = usage.prompt_tokens_details if usage else None
        extra = (usage.model_extra or {}) if usage else {}
        prompt = usage.prompt_tokens if usage else 0
        output = usage.completion_tokens if usage else 0
        cached = (details.cached_tokens or 0) if details else 0
        cost = float(extra.get("cost") or 0.0)
        meter = self.meter
        meter.calls += 1
        meter.prompt_tokens += prompt
        meter.completion_tokens += output
        meter.cached_tokens += cached
        meter.cost += cost
        entry = {
            "time": time.time(),
            "model": completion.model,
            "prompt_tokens": prompt,
            "completion_tokens": output,
            "cached_tokens": cached,
            "cost": cost,
        }
        with self._usage_log.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")


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


def rate(results: dict[Any, bool]) -> float | None:
    """Share of ``True`` results, or ``None`` when there are none."""
    return sum(results.values()) / len(results) if results else None


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

    # Only the project's .env: a key exported in the shell may belong to
    # something else and lack this project's spend limit.
    api_key = dotenv_values(REPO / ".env").get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY is not set in .env", file=sys.stderr)  # noqa: T201 - CLI
        return 2

    run_dir = HERE / "runs" / args.run_name
    openrouter = openai.OpenAI(base_url=OPENROUTER, api_key=api_key)
    client = MeteredClient(openrouter, run_dir, args.max_cost)
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
    summary_file = run_dir / f"summary-{args.mode}.json"
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
        summary["utility"] = rate(results["utility_results"])
        summary["utility_tasks"] = len(results["utility_results"])
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
        summary["utility_under_attack"] = rate(results["utility_results"])
        # AgentDojo's security result is True when the injection task succeeded.
        summary["asr"] = rate(results["security_results"])
        summary["pairs"] = len(results["security_results"])


if __name__ == "__main__":
    sys.exit(main())
