# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""What the runtime asks of a language model, whatever serves it.

One kind of call: structured output against a JSON schema. The system prompt
is the stable part of a request (a provider may cache it), the user content
the part that changes. A reply is the parsed JSON object; anything else is a
typed ``LLMError`` whose message names what went wrong, never the reply's
text, which may carry an injection.
"""

import dataclasses
from collections.abc import Mapping
from typing import Protocol

type Json = Mapping[str, object]


@dataclasses.dataclass(frozen=True, slots=True)
class StructuredRequest:
    """One structured-output call."""

    model: str
    system: str
    """Stable across calls: instructions a provider may cache."""
    user: str
    """What changes from call to call."""
    schema_name: str
    schema: Json
    """A strict JSON schema the reply object must follow."""
    max_tokens: int
    """The output budget, reasoning included: a reply cut at it is an error."""
    reasoning_effort: str | None = None
    """``low``, ``medium`` or ``high`` for a reasoning model; ``None`` to omit."""


@dataclasses.dataclass(frozen=True, slots=True)
class Usage:
    """What a call consumed, as the provider reports it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class StructuredReply:
    """The reply object and what it cost."""

    value: Json
    usage: Usage = Usage()
    cached: bool = False
    """Served from the response cache, at no cost."""


class LLMClient(Protocol):
    """A model behind a structured-output call; its reply follows the request's schema."""

    def structured(self, request: StructuredRequest) -> StructuredReply:
        """Ask ``request``; the reply follows its schema or an ``LLMError`` is raised."""
        ...


class LLMError(RuntimeError):
    """A model call did not give a usable reply; the message never quotes the reply.

    ``usage`` is what the failed call was billed, as the provider reported it
    (a reply cut at its budget is billed in full); zero if nothing came back.
    """

    usage: Usage = Usage()


class TruncatedError(LLMError):
    """The reply was cut at the output budget (``finish_reason == "length"``)."""

    def __init__(self, model: str, usage: Usage = Usage()) -> None:  # noqa: B008 - frozen
        super().__init__(f"{model}: reply cut at the output budget")
        self.usage = usage


class MalformedReplyError(LLMError):
    """The reply is not a JSON object, or not one of the requested schema."""

    def __init__(self, model: str, what: str, usage: Usage = Usage()) -> None:  # noqa: B008 - frozen
        super().__init__(f"{model}: malformed reply ({what})")
        self.usage = usage


class ProviderError(LLMError):
    """The provider refused or failed the request, or could not be reached."""

    def __init__(self, model: str, status: int | None) -> None:
        what = "provider unreachable" if status is None else f"provider error (HTTP {status})"
        super().__init__(f"{model}: {what}")


class UnsupportedSchemaError(LLMError):
    """The request's schema uses something no reply could be checked against."""

    def __init__(self, model: str) -> None:
        super().__init__(f"{model}: unsupported schema, nothing was sent")


class BudgetExceededError(LLMError):
    """The client has spent what it may: no more calls."""

    def __init__(self, model: str) -> None:
        super().__init__(f"{model}: budget exceeded, no further calls")
