# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""What the planner (P-LLM) reads: the language, the tools, the request, its mistakes.

The system prompt is the stable part, so a provider may cache it: the role,
the plan language's rules (built from the core's own lists, so they cannot
drift from what ``parse_plan`` accepts), and every tool's signature from
``Registry.describe()``. The planner never sees data, so the user message
holds the owner's request and, on a retry, the previous program and its
typed errors: a code, a line and a name from the program itself (Q3).
"""

from collections.abc import Sequence
from typing import Final

from taintwall.core.errors import PlanError
from taintwall.core.interpreter import BUILTINS
from taintwall.core.syntax import STRING_METHODS

READER_TOOL: Final = "query_reader"
"""The reader's tool name: the prompt mentions it only when it is there."""

PLAN_SCHEMA: Final = {
    "type": "object",
    "properties": {
        "plan": {"type": "string", "description": "The program's Python source."},
    },
    "required": ["plan"],
    "additionalProperties": False,
}
"""The planner's reply: one field, the program."""


def _names(names: frozenset[str], form: str) -> str:
    return ", ".join(form.format(name) for name in sorted(names))


def rules() -> str:
    """The plan language's rules, with the core's own lists of builtins and methods.

    Built when a prompt is, not at import, so a change to those lists shows here.
    """
    return f"""\
The program runs in a restricted Python subset. Use only:

- assignments to one name (`x = ...`), `x += y` (also `-=` and `*=`), and
  expression statements;
- `if`/`elif`/`else`, `for name in tuple_or_list:`, `while`, `break`,
  `continue`, `pass`;
- literals (text, numbers, `True`, `False`, `None`), tuples, lists, and
  records written as `{{"field": value}}` with string keys;
- `+ - * / // %`, comparisons (including `in`, `is None`, `is not None`),
  `and`, `or`, `not`, and `a if condition else b`;
- `record.field`, `items[0]`, `record["key"]`, `record.get("key")`;
- the string methods {_names(STRING_METHODS, "`.{}()`")};
- f-strings, with a fixed-point format for numbers such as `f"{{x:.2f}}"`;
- calls to the functions below and to the builtins {_names(BUILTINS, "`{}`")}.

Only text, numbers, booleans and `None` can be formatted in an f-string or
passed to `str()`: format a record's fields, not the record. Not allowed:
imports, `def`, `class`, `lambda`, comprehensions, other methods, slices,
`x[i] = v`, `try`, `**`, names starting with `_`, and names that are not
plain ASCII. Values cannot be
changed in place: build a new tuple with `items = items + (x,)`."""


SYSTEM: Final = """\
You are the planner of an assistant. You never see the user's data: you write
a Python program that completes the user's request, and a runtime executes it.

Call the functions below to read and change things. Their results are data
you cannot see.{reader} When the program is done, call `answer(text)` with
what to tell the user.

Reply with a JSON object whose "plan" is the program's source.

{rules}

Functions:

{functions}
"""


READER: Final = """ When you need information from unstructured data (the text
of a file, an email), pass it to `query_reader` with the fields you need."""


def system(functions: str, *, has_reader: bool) -> str:
    """The planner's system prompt, for the tools that ``functions`` describes."""
    return SYSTEM.format(reader=READER if has_reader else "", rules=rules(), functions=functions)


def request(text: str) -> str:
    """The first user message: the owner's request."""
    return f"Request: {text}"


def static_retry(text: str, source: str, errors: Sequence[PlanError]) -> str:
    """The request again, the program that cannot run, and why, in typed terms."""
    return (
        f"{request(text)}\n\nYour previous program:\n<program>\n{source}\n</program>\n\n"
        f"It cannot run as written:\n{_errors(errors)}\n\nWrite the whole program again."
    )


def runtime_retry(text: str, source: str, error: PlanError) -> str:
    """The request again, the program that stopped, and where; nothing it read."""
    return (
        f"{request(text)}\n\nYour previous program:\n<program>\n{source}\n</program>\n\n"
        f"It stopped while running, before it changed anything:\n{_errors([error])}\n\n"
        "Write the whole program again."
    )


def _errors(errors: Sequence[PlanError]) -> str:
    """One line per error: its line, its code and the name it is about."""
    return "\n".join(
        f"- line {error.line if error.line is not None else '?'}: {error.code.value}"
        + (f" ({error.detail})" if error.detail else "")
        for error in errors
    )
