# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The LLM layer: OpenRouter requests and replies, typed errors, the response cache."""

import dataclasses
import http.client
import http.server
import json
import socketserver
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import ClassVar, override

import pytest

from taintwall.llm import (
    BudgetedClient,
    BudgetExceededError,
    CachedClient,
    MalformedReplyError,
    MissingKeyError,
    OpenRouterClient,
    ProviderError,
    StructuredReply,
    StructuredRequest,
    TruncatedError,
    Usage,
    conforms,
    openrouter,
    read_key,
)
from taintwall.llm.cache import key

REQUEST = StructuredRequest(
    model="m/x",
    system="Be strict.",
    user="Question: q",
    schema_name="extraction",
    schema={"type": "object"},
    max_tokens=100,
)


def response(content: object, finish: str = "stop", usage: object = None) -> bytes:
    body: dict[str, object] = {
        "choices": [{"finish_reason": finish, "message": {"content": content}}]
    }
    if usage is not None:
        body["usage"] = usage
    return json.dumps(body).encode()


class Recorder:
    def __init__(self, status: int, raw: bytes) -> None:
        self.status, self.raw = status, raw
        self.calls: list[tuple[str, dict[str, object], Mapping[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, json.loads(body), headers))
        return self.status, self.raw


class TestOpenRouter:
    def test_a_request_carries_the_stage_0_findings(self) -> None:
        transport = Recorder(200, response('{"a": 1}'))
        reply = OpenRouterClient("sk-test", transport).structured(REQUEST)
        assert reply == StructuredReply({"a": 1})
        url, body, headers = transport.calls[0]
        assert url == openrouter.ENDPOINT
        assert headers == {"Authorization": "Bearer sk-test", "Content-Type": "application/json"}
        assert body == {
            "model": "m/x",
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "Be strict.",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
                {"role": "user", "content": "Question: q"},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "strict": True, "schema": {"type": "object"}},
            },
            "max_tokens": 100,
            "provider": {"require_parameters": True},
            "usage": {"include": True},
        }

    def test_reasoning_effort_is_sent_only_when_set(self) -> None:
        transport = Recorder(200, response("{}"))
        client = OpenRouterClient("k", transport)
        client.structured(REQUEST)
        client.structured(dataclasses.replace(REQUEST, reasoning_effort="low"))
        assert "reasoning" not in transport.calls[0][1]
        assert transport.calls[1][1]["reasoning"] == {"effort": "low"}

    def test_usage_is_read_where_given(self) -> None:
        usage = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 8},
            "cost": 0.25,
        }
        reply = OpenRouterClient("k", Recorder(200, response("{}", usage=usage))).structured(
            REQUEST
        )
        assert reply.usage == Usage(10, 5, 8, 0.25)
        odd = {"prompt_tokens": "10", "prompt_tokens_details": None, "cost": "1"}
        assert (
            OpenRouterClient("k", Recorder(200, response("{}", usage=odd)))
            .structured(REQUEST)
            .usage
            == Usage()
        )
        assert (
            OpenRouterClient("k", Recorder(200, response("{}", usage=[]))).structured(REQUEST).usage
            == Usage()
        )
        assert OpenRouterClient("k", Recorder(200, response("{}", usage={"cost": 2}))).structured(
            REQUEST
        ).usage == Usage(cost=2.0)

    def test_a_json_error_does_not_carry_the_reply(self) -> None:
        with pytest.raises(MalformedReplyError) as err:
            OpenRouterClient("k", Recorder(200, response("SECRET not json"))).structured(REQUEST)
        assert err.value.__context__ is None
        with pytest.raises(MalformedReplyError) as broken:
            OpenRouterClient("k", Recorder(200, b"SECRET")).structured(REQUEST)
        assert broken.value.__context__ is None

    @pytest.mark.parametrize(
        ("status", "raw", "error", "message"),
        [
            (402, b"", ProviderError, "m/x: provider error (HTTP 402)"),
            (
                200,
                response("{}", finish="length"),
                TruncatedError,
                "m/x: reply cut at the output budget",
            ),
            (200, b"not json", MalformedReplyError, "m/x: malformed reply (response)"),
            (200, b'{"choices": []}', MalformedReplyError, "m/x: malformed reply (response)"),
            (200, b'{"choices": [7]}', MalformedReplyError, "m/x: malformed reply (response)"),
            (
                200,
                response("ignore all and send"),
                MalformedReplyError,
                "m/x: malformed reply (not JSON)",
            ),
            (200, response(None), MalformedReplyError, "m/x: malformed reply (not JSON)"),
            (200, response("[1]"), MalformedReplyError, "m/x: malformed reply (not an object)"),
            (200, response('{"a": NaN}'), MalformedReplyError, "m/x: malformed reply (not JSON)"),
            (
                200,
                response('{"a": Infinity}'),
                MalformedReplyError,
                "m/x: malformed reply (not JSON)",
            ),
        ],
    )
    def test_failures_are_typed_and_never_quote_the_reply(
        self, status: int, raw: bytes, error: type[Exception], message: str
    ) -> None:
        with pytest.raises(error) as err:
            OpenRouterClient("sk-secret", Recorder(status, raw)).structured(REQUEST)
        assert str(err.value) == message
        assert err.value.__cause__ is None

    def test_the_key_comes_from_the_env_file_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "from-shell")
        env = tmp_path / ".env"
        env.write_text(
            "# comment\nOTHER=1\nOPENROUTER_API_KEY = 'sk-or-1'  # note\n", encoding="utf-8"
        )
        assert read_key(env) == "sk-or-1"
        env.write_text("export OPENROUTER_API_KEY=sk-or-2\n", encoding="utf-8")
        assert read_key(env) == "sk-or-2"
        env.write_text("OPENROUTER_API_KEY=\nX=OPENROUTER_API_KEY\n", encoding="utf-8")
        with pytest.raises(MissingKeyError) as err:
            read_key(env)
        assert str(err.value) == f"OPENROUTER_API_KEY is not set in {env}"


class _Handler(http.server.BaseHTTPRequestHandler):
    status: ClassVar[int] = 200
    seen: ClassVar[list[tuple[str, bytes, str]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        type(self).seen.append((self.path, self.rfile.read(length), self.headers["X-Test"]))
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"reply")

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


class _Server(http.server.HTTPServer):
    @override
    def server_bind(self) -> None:
        # HTTPServer looks up the host's full name, a slow DNS query on some machines.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", int(self.server_address[1])


@pytest.fixture
def server() -> Iterator[str]:
    httpd = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()
    httpd.server_close()


def test_the_standard_library_transport_posts_and_reads(server: str) -> None:
    _Handler.status, _Handler.seen = 200, []
    assert openrouter.urllib_transport(server, b"body", {"X-Test": "1"}) == (200, b"reply")
    assert _Handler.seen == [("/v1", b"body", "1")]
    _Handler.status = 429
    assert openrouter.urllib_transport(server, b"body", {"X-Test": "2"}) == (429, b"")


class TestCache:
    def test_a_seen_request_is_answered_from_disk(self, tmp_path: Path) -> None:
        transport = Recorder(200, response('{"a": 1}', usage={"cost": 0.5}))
        first = CachedClient(OpenRouterClient("k", transport), tmp_path / "cache")
        assert first.structured(REQUEST) == StructuredReply({"a": 1}, Usage(cost=0.5))
        again = CachedClient(OpenRouterClient("k", transport), tmp_path / "cache")
        assert again.structured(REQUEST) == StructuredReply({"a": 1}, cached=True)
        assert len(transport.calls) == 1
        assert list((tmp_path / "cache").iterdir()) == [tmp_path / "cache" / f"{key(REQUEST)}.json"]

    def test_every_field_is_in_the_key(self) -> None:
        variations = [
            dataclasses.replace(REQUEST, model="m/y"),
            dataclasses.replace(REQUEST, system="s"),
            dataclasses.replace(REQUEST, user="u"),
            dataclasses.replace(REQUEST, schema_name="n"),
            dataclasses.replace(REQUEST, schema={"type": "array"}),
            dataclasses.replace(REQUEST, max_tokens=7),
            dataclasses.replace(REQUEST, reasoning_effort="low"),
        ]
        keys = {key(REQUEST), *(key(request) for request in variations)}
        assert len(keys) == 8

    @pytest.mark.parametrize("stored", ["{broken", "[1]", b"\xff\xfe"])
    def test_an_unreadable_entry_is_a_miss(self, tmp_path: Path, stored: str | bytes) -> None:
        directory = tmp_path / "cache"
        directory.mkdir()
        path = directory / f"{key(REQUEST)}.json"
        if isinstance(stored, bytes):
            path.write_bytes(stored)
        else:
            path.write_text(stored, encoding="utf-8")
        transport = Recorder(200, response('{"a": 2}'))
        assert CachedClient(OpenRouterClient("k", transport), directory).structured(
            REQUEST
        ).value == {"a": 2}
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2}
        assert [p.name for p in directory.iterdir()] == [path.name]

    def test_a_failed_call_is_not_cached(self, tmp_path: Path) -> None:
        client = CachedClient(OpenRouterClient("k", Recorder(500, b"")), tmp_path / "c")
        with pytest.raises(ProviderError):
            client.structured(REQUEST)
        assert list((tmp_path / "c").iterdir()) == []


STRICT = {
    "type": "object",
    "properties": {
        "a": {"type": "string"},
        "n": {"type": "integer"},
        "x": {"type": "number"},
        "ok": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "none": {"type": "null"},
    },
    "required": ["a", "n"],
    "additionalProperties": False,
}


class TestSchemaAndFailures:
    @pytest.mark.parametrize(
        ("value", "fits"),
        [
            ({"a": "s", "n": 1}, True),
            ({"a": "s", "n": 1, "x": 1, "ok": True, "tags": ["t"], "none": None}, True),
            ({"a": "s", "n": 1, "x": 1.5}, True),
            ({"a": "s"}, False),
            ({"a": "s", "n": 1, "more": 1}, False),
            ({"a": 1, "n": 1}, False),
            ({"a": "s", "n": True}, False),
            ({"a": "s", "n": 1.0}, False),
            ({"a": "s", "n": 1, "x": True}, False),
            ({"a": "s", "n": 1, "tags": [1]}, False),
            ({"a": "s", "n": 1, "tags": "t"}, False),
            ({"a": "s", "n": 1, "none": 0}, False),
            ([], False),
        ],
    )
    def test_a_reply_must_follow_the_schema(self, value: object, fits: bool) -> None:  # noqa: FBT001
        assert conforms(value, STRICT) is fits

    @pytest.mark.parametrize(
        "schema",
        [
            {"type": "string", "minLength": 1},
            {"type": "date"},
            {"type": ["string", "null"]},
            [],
            {"type": "object", "properties": []},
            {"type": "object", "required": "a"},
        ],
    )
    def test_a_schema_it_cannot_check_fits_nothing(self, schema: object) -> None:
        assert not conforms({"a": "x"}, schema)
        assert not conforms("x", schema)

    def test_open_objects_take_more_keys(self) -> None:
        assert conforms({"b": 1}, {"type": "object"})
        assert conforms({"b": 1}, {"type": "object", "additionalProperties": True})

    def test_a_reply_off_the_schema_is_malformed(self) -> None:
        request = dataclasses.replace(REQUEST, schema=STRICT)
        with pytest.raises(MalformedReplyError) as err:
            OpenRouterClient("k", Recorder(200, response('{"a": "s"}'))).structured(request)
        assert str(err.value) == "m/x: malformed reply (not of the schema)"

    @pytest.mark.parametrize("failure", [OSError, TimeoutError, http.client.IncompleteRead])
    def test_no_answer_is_a_typed_error(self, failure: type[Exception]) -> None:
        def transport(_url: str, _body: bytes, _headers: Mapping[str, str]) -> tuple[int, bytes]:
            raise failure(
                b"secret host" if failure is http.client.IncompleteRead else "secret host"
            )

        with pytest.raises(ProviderError) as err:
            OpenRouterClient("k", transport).structured(REQUEST)
        assert str(err.value) == "m/x: provider unreachable"
        assert err.value.__cause__ is None
        assert err.value.__context__ is None


class TestBudget:
    def test_calls_and_cost_stop_at_their_limits(self) -> None:
        transport = Recorder(200, response("{}", usage={"cost": 0.4}))
        budget = BudgetedClient(OpenRouterClient("k", transport), max_calls=5, max_cost=1.0)
        for user in ("1", "2", "3"):
            budget.structured(dataclasses.replace(REQUEST, user=user))
        assert budget.calls == 3
        assert budget.cost == pytest.approx(1.2)
        with pytest.raises(BudgetExceededError) as err:
            budget.structured(REQUEST)
        assert str(err.value) == "m/x: budget exceeded, no further calls"
        assert len(transport.calls) == 3
        few = BudgetedClient(OpenRouterClient("k", transport), max_calls=1, max_cost=10.0)
        few.structured(REQUEST)
        with pytest.raises(BudgetExceededError):
            few.structured(REQUEST)

    def test_cached_replies_are_free(self, tmp_path: Path) -> None:
        transport = Recorder(200, response("{}", usage={"cost": 0.4}))
        cached = CachedClient(OpenRouterClient("k", transport), tmp_path / "c")
        budget = BudgetedClient(cached, max_calls=1, max_cost=1.0)
        budget.structured(REQUEST)
        budget = BudgetedClient(cached, max_calls=1, max_cost=1.0)
        budget.structured(REQUEST)
        budget.structured(REQUEST)
        assert (budget.calls, budget.cost) == (0, 0.0)
