# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The taintwall planner as an AgentDojo pipeline element.

``PlannerAgent.query`` builds the suite's functions over the task's
environment (``registry.entries``) and the reader, runs ``Planner`` on the
prompt, and replies with what the plan answered. ``Watched`` stands between
the planner and its model: a provider failure or a spent budget must stop the
run, not be scored as a planner failure.
"""

from collections.abc import Sequence
from typing import Any

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.models import MODEL_NAMES
from agentdojo.types import ChatMessage, text_content_block_from_string
from bench_common.naming import prose_name
from registry import entries

from taintwall.core.mint import Mint
from taintwall.llm import (
    BudgetExceededError,
    LLMClient,
    LLMError,
    ProviderError,
    StructuredReply,
    StructuredRequest,
)
from taintwall.planner import (
    PLAN_FIELD,
    PLAN_SCHEMA_NAME,
    Outcome,
    Planner,
    PlannerModel,
    Replan,
)
from taintwall.reader import reader
from taintwall.tools import Implementation, ToolSpec


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
        self.calls = 0
        """Model calls since the last ``reset``, planner and reader, cached or not."""
        self.cost = 0.0
        """What those calls were billed; a reply from the cache costs nothing."""

    def reset(self) -> None:
        """Start counting one request's calls and plans."""
        self.plans.clear()
        self.calls = 0
        self.cost = 0.0

    def structured(self, request: StructuredRequest) -> StructuredReply:
        """The inner reply; a provider failure or a spent budget is noted, then raised."""
        self.calls += 1
        try:
            reply = self._inner.structured(request)
        except (ProviderError, BudgetExceededError) as err:
            self.stop = self.stop or str(err)
            raise
        except LLMError as err:
            self.cost += err.usage.cost
            raise
        self.cost += reply.usage.cost
        if request.schema_name == PLAN_SCHEMA_NAME:
            self.plans.append(str(reply.value.get(PLAN_FIELD)))
        return reply


class PlannerAgent(BasePipelineElement):  # type: ignore[misc]  # AgentDojo is untyped
    """The planner as an AgentDojo pipeline: the prompt in, the answer out."""

    def __init__(  # noqa: PLR0913 - the run's settings
        self,
        client: Watched,
        planner: PlannerModel,
        *,
        reader_model: str,
        reader_effort: str | None,
        reader_calls: int,
        replan: Replan,
        suite: Any,  # noqa: ANN401 - AgentDojo's TaskSuite is untyped
        suite_name: str,
    ) -> None:
        self.client = client
        self._planner = planner
        self._reader_model = reader_model
        self._reader_effort = reader_effort
        self._reader_calls = reader_calls
        self._replan = replan
        self._suite = suite
        self._suite_name = suite_name
        self._mint = Mint()
        self.name = planner.name.rsplit("/", 1)[-1] + "-taintwall"
        # The important_instructions attack addresses the model by name.
        MODEL_NAMES.setdefault(self.name, prose_name(planner.name))
        self.outcome: Outcome | None = None
        self.refused: dict[str, str] = {}
        """Functions the registry cannot express, left out of every request, with why."""
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
            found, refused = entries(self._suite, env, self._suite_name)
            self.refused.update(refused)
            ask = reader(
                self.client,
                self._reader_model,
                max_calls=self._reader_calls,
                reasoning_effort=self._reader_effort,
            )
            return [*found, ask]

        planner = Planner(self.client, self._planner, tools, replan=self._replan)
        self.client.reset()
        self.outcome = planner.run(query, requester=self._mint.owner(), mint=self._mint)
        if self.client.stop is not None:
            raise StopRunError(self.client.stop)
        self._record(query, self.outcome)
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

    def _record(self, query: str, outcome: Outcome) -> None:
        """Keep what the planner wrote and said for this query."""
        self.transcripts.append(
            {
                "query": query,
                "plans": list(self.client.plans),
                "errors": [[str(error) for error in a.errors] for a in outcome.attempts],
                "failure": outcome.failure,
                "answers": [answer.text for answer in outcome.answers],
            }
        )
