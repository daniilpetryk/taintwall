# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""A ceiling on what a client may spend: calls and cost.

A run that reaches either limit stops calling: the next request raises
``BudgetExceededError`` before it reaches the provider. The call that
crosses the cost ceiling still completes (its cost is known only after),
so a run can end up one call over. Replies from the cache cost nothing and
do not count. A call that fails counts, with what it was billed (a reply cut
at its budget is billed in full), unless it never reached a provider.
"""

from taintwall.llm.client import (
    BudgetExceededError,
    LLMClient,
    LLMError,
    StructuredReply,
    StructuredRequest,
    UnsupportedSchemaError,
)


class BudgetedClient:
    """An ``LLMClient`` that refuses calls past ``max_calls`` or ``max_cost``."""

    def __init__(self, inner: LLMClient, *, max_calls: int, max_cost: float) -> None:
        self._inner = inner
        self._max_calls = max_calls
        self._max_cost = max_cost
        self.calls = 0
        self.cost = 0.0

    def structured(self, request: StructuredRequest) -> StructuredReply:
        """The inner client's reply, while the budget lasts.

        Raises:
            BudgetExceededError: the calls or the cost have reached their limit.
            LLMError: the inner client's error, counted first.
        """
        if self.calls >= self._max_calls or self.cost >= self._max_cost:
            raise BudgetExceededError(request.model)
        reply = _call(self._inner, request)
        if isinstance(reply, LLMError):
            # Refused before any call: a schema no reply could be checked
            # against, or an inner budget already spent.
            if not isinstance(reply, UnsupportedSchemaError | BudgetExceededError):
                self.calls += 1
                self.cost += reply.usage.cost
            raise reply
        if not reply.cached:
            self.calls += 1
            self.cost += reply.usage.cost
        return reply


def _call(client: LLMClient, request: StructuredRequest) -> StructuredReply | LLMError:
    """The reply or the error, so the error is raised again outside any handler."""
    try:
        return client.structured(request)
    except LLMError as err:
        return err
