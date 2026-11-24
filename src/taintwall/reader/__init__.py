# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The reader (Q-LLM) as the tool ``query_reader``.

It joins the tool registry and the LLM layer in a package of its own, so
that ``taintwall.tools`` never imports a model (an import contract) and the
policies that need tools never reach an LLM through them.
"""

from taintwall.reader.query import (
    ENOUGH,
    MAX_FIELDS,
    NAME,
    SPEC,
    SYSTEM,
    BudgetError,
    NotEnoughInfoError,
    ReaderSchemaError,
    ReplyTypeError,
    check_reply,
    compile_schema,
    fenced,
    prompt,
    reader,
)

__all__ = [
    "ENOUGH",
    "MAX_FIELDS",
    "NAME",
    "SPEC",
    "SYSTEM",
    "BudgetError",
    "NotEnoughInfoError",
    "ReaderSchemaError",
    "ReplyTypeError",
    "check_reply",
    "compile_schema",
    "fenced",
    "prompt",
    "reader",
]
