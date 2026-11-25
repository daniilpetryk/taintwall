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
    ProviderError,
    StructuredReply,
    StructuredRequest,
)
from taintwall.planner import Outcome, Planner, PlannerModel, Replan
from taintwall.reader import reader
from taintwall.tools import Implementation, ToolSpec

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
                "plans": list(self._client.plans),
                "errors": [[str(error) for error in a.errors] for a in outcome.attempts],
                "failure": outcome.failure,
                "answers": [answer.text for answer in outcome.answers],
            }
        )
