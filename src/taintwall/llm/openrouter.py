# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Structured-output calls through OpenRouter's OpenAI-compatible API.

What stage 0 found shapes every request (``docs/design.md``, Stack):

* ``provider.require_parameters`` is set, so OpenRouter routes only to
  providers that enforce the JSON schema (two of Haiku's do not);
* no sampling parameters are sent (no provider of a reasoning model takes
  them, and with ``require_parameters`` they would fail the request);
* the system prompt carries a ``cache_control`` breakpoint, without which
  nothing is cached.

The key comes from the repo's ``.env`` file only, never from the shell
environment (a key exported there may lack this project's spend limit), and
never appears in an error. The transport is a parameter, so tests never
touch the network.
"""

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from taintwall.llm.client import (
    Json,
    MalformedReplyError,
    ProviderError,
    StructuredReply,
    StructuredRequest,
    TruncatedError,
    UnsupportedSchemaError,
    Usage,
)
from taintwall.llm.conform import conforms, supported

ENDPOINT: Final = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS: Final = 120

RETRY_DELAYS: Final = (2.0, 8.0)
"""Seconds to wait before each retry of a rate limit, a server error or no answer."""

MAX_WAIT: Final = 60.0
"""The longest a ``Retry-After`` may make a retry wait."""

type Answer = tuple[int, bytes, float | None]
"""The status, the response body, and the seconds a ``Retry-After`` asks to wait."""

type Transport = Callable[[str, bytes, Mapping[str, str]], Answer]
"""POST ``body`` to ``url`` with ``headers``; the provider's answer."""


class MissingKeyError(RuntimeError):
    """The ``.env`` file has no ``OPENROUTER_API_KEY``."""

    def __init__(self, env_file: Path) -> None:
        super().__init__(f"OPENROUTER_API_KEY is not set in {env_file}")


def read_key(env_file: Path) -> str:
    """``OPENROUTER_API_KEY`` from a ``KEY=value`` file (quotes and comments allowed).

    Raises:
        MissingKeyError: the file has no such key, or it is empty.
    """
    text = env_file.read_text(encoding="utf-8")  # pragma: no mutate - the default here
    for line in text.splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and name.strip() == "OPENROUTER_API_KEY":
            key = value.partition(" #")[0].strip().strip("'\"")
            if key:
                return key
    raise MissingKeyError(env_file)


def urllib_transport(url: str, body: bytes, headers: Mapping[str, str]) -> Answer:
    """POST with the standard library; an HTTP error is a status, not an exception."""
    # POST and the timeout are what urllib would pick here; stated so they stay.
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")  # noqa: S310  # pragma: no mutate
    try:
        # Without the timeout a stalled provider would hang the run; tests cannot wait it out.
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310  # pragma: no mutate
            return int(response.status), response.read(), None
    except urllib.error.HTTPError as err:
        err.close()
        # Header names are case-insensitive, so a mutant's other case reads the same header.
        return err.code, b"", _seconds(err.headers.get("Retry-After"))  # pragma: no mutate


def _seconds(header: str | None) -> float | None:
    """A ``Retry-After`` given in seconds; ``None`` for none or an HTTP date."""
    return float(header) if header is not None and header.strip().isdigit() else None


class OpenRouterClient:
    """An ``LLMClient`` for any model OpenRouter serves."""

    def __init__(
        self,
        api_key: str,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._transport = transport
        self._sleep = sleep or time.sleep

    def structured(self, request: StructuredRequest) -> StructuredReply:
        """Ask the model; the reply object, or a typed ``LLMError``.

        A rate limit (429), a server error (5xx) or no answer is retried after
        each of ``RETRY_DELAYS``, or longer if the provider's ``Retry-After``
        asks (up to ``MAX_WAIT``). A provider bills a completed reply: if the
        connection drops after the reply was generated, the retry may be
        billed again, and only the reply that arrives counts in ``Usage``.

        Raises:
            UnsupportedSchemaError: a schema no reply could be checked against;
                nothing is sent.
            ProviderError: a status other than 200, or no answer at all, after
                the retries.
            TruncatedError: the reply was cut at ``max_tokens``.
            MalformedReplyError: no JSON object of the request's schema.
        """
        if not supported(request.schema):
            raise UnsupportedSchemaError(request.model)
        data = json.dumps(body(request)).encode()
        answer = _post(self._transport, data, self._headers)
        for delay in RETRY_DELAYS:
            if not _transient(answer):
                break
            asked = None if answer is None else answer[2]
            self._sleep(delay if asked is None else max(delay, min(asked, MAX_WAIT)))
            answer = _post(self._transport, data, self._headers)
        # Raised outside any handler: no exception rides along as context.
        if answer is None:
            raise ProviderError(request.model, None)
        status, raw, _ = answer
        if status != 200:  # noqa: PLR2004 - HTTP OK
            raise ProviderError(request.model, status)
        reply = parse(request.model, raw)
        if not conforms(dict(reply.value), request.schema):
            raise MalformedReplyError(request.model, "not of the schema")
        return reply


def _transient(answer: Answer | None) -> bool:
    """No answer, a rate limit or a server error: worth asking again."""
    return answer is None or answer[0] == 429 or answer[0] >= 500  # noqa: PLR2004 - HTTP codes


def _post(transport: Transport, data: bytes, headers: Mapping[str, str]) -> Answer | None:
    """The transport's answer, or ``None`` if there was none (refused, timed out, cut off)."""
    try:
        return transport(ENDPOINT, data, headers)
    except (OSError, http.client.HTTPException):
        return None


def body(request: StructuredRequest) -> Json:
    """The request body OpenRouter takes for ``request``."""
    payload: dict[str, object] = {
        "model": request.model,
        "messages": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": request.system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {"role": "user", "content": request.user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
        },
        "max_tokens": request.max_tokens,
        "provider": {"require_parameters": True},
        "usage": {"include": True},
    }
    if request.reasoning_effort is not None:
        payload["reasoning"] = {"effort": request.reasoning_effort}
    return payload


def parse(model: str, raw: bytes) -> StructuredReply:
    """The reply object of an OpenRouter response.

    Every error is raised outside the handler that found it: a JSON error
    carries the whole text it failed on, the reply's, and must not ride along
    as ``__context__``.

    Raises:
        TruncatedError: the reply was cut at the output budget.
        MalformedReplyError: no JSON object where the reply should be.
    """
    found = _choice(raw)
    if found is None:
        raise MalformedReplyError(model, "response")
    finish, content, usage = found
    if finish == "length":
        raise TruncatedError(model)
    value = _json(content)
    if value is _NOT_JSON:
        raise MalformedReplyError(model, "not JSON")
    if not isinstance(value, dict):
        raise MalformedReplyError(model, "not an object")
    return StructuredReply(value, _usage(usage))


_NOT_JSON: Final = object()


def _choice(raw: bytes) -> tuple[object, object, object] | None:
    """The first choice's finish reason and content, and the usage; ``None`` if absent."""
    try:
        response = json.loads(raw)
        choice = response["choices"][0]
        return choice.get("finish_reason"), choice["message"]["content"], response.get("usage")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None


def _json(content: object) -> object:
    """``content`` parsed as JSON, or ``_NOT_JSON``."""
    try:
        return json.loads(content, parse_constant=_no_constant)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return _NOT_JSON


def _no_constant(_name: str) -> object:
    """``NaN`` and ``Infinity`` are not JSON: a reply that has them is malformed."""
    raise ValueError


def _usage(usage: object) -> Usage:
    """Token counts and cost, zero where the provider gives none."""
    if not isinstance(usage, dict):
        return Usage()
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0
    return Usage(
        prompt_tokens=_count(usage.get("prompt_tokens")),
        completion_tokens=_count(usage.get("completion_tokens")),
        cached_tokens=_count(cached),
        cost=float(usage["cost"]) if type(usage.get("cost")) in {int, float} else 0.0,
    )


def _count(value: object) -> int:
    return value if type(value) is int else 0
