# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The plan-collection study's prompt and parsing, without calling any model."""

from types import SimpleNamespace

from agentdojo.task_suite.load_suites import get_suite
from plans import BENCHMARK_VERSION, extract_code, signature, system_prompt


def test_the_first_python_block_is_the_plan() -> None:
    reply = "Here:\n```python\nx = 1\n```\nand\n```python\ny = 2\n```"
    assert extract_code(reply) == "x = 1\n"
    assert extract_code("```\nz = 3\n```") == "z = 3\n"


def test_a_cut_reply_has_no_plan() -> None:
    assert extract_code("```python\nx = get_balance()\nif x") is None


def test_signatures_show_types_optionals_and_defaults() -> None:
    suite = get_suite(BENCHMARK_VERSION, "banking")
    by_name = {tool.name: signature(tool) for tool in suite.tools}
    assert by_name["send_money"].startswith(
        "def send_money(recipient: str, amount: float, subject: str, date: str) -> dict[str, str]:"
    )
    assert "recipient: str | None = None" in by_name["update_scheduled_transaction"]
    assert "(n: int = 100) -> list[Transaction]" in by_name["get_most_recent_transactions"]


def test_rules_are_stated_only_when_asked() -> None:
    suite = SimpleNamespace(tools=[])
    assert "restricted Python subset" not in system_prompt(suite)
    assert "restricted Python subset" in system_prompt(suite, rules=True)
