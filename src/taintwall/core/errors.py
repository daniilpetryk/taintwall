# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Typed plan errors: the only thing about a failure the planner may learn.

Decision Q3: on a failure the P-LLM gets a code, never text. Error messages
and tool output can carry an injection, so a ``PlanError`` holds only a code,
the plan line, and a detail taken from the plan itself (a variable, field or
tool name the planner wrote), never anything derived from data.
"""

import enum


class PlanErrorCode(enum.StrEnum):
    """Why a plan was rejected or stopped."""

    TOO_LARGE = "too_large"
    """The plan source is longer than the limit."""

    SYNTAX = "syntax"
    """The plan is not valid Python."""

    UNSUPPORTED = "unsupported"
    """The plan uses a construct outside the plan language."""

    TOO_DEEP = "too_deep"
    """The plan nests expressions deeper than the limit."""

    UNKNOWN_NAME = "unknown_name"
    """A variable is read before it is assigned."""

    UNKNOWN_TOOL = "unknown_tool"
    """A call names neither a tool nor a builtin."""

    BAD_ARGUMENTS = "bad_arguments"
    """A builtin was called with the wrong number or kind of arguments."""

    TYPE_MISMATCH = "type_mismatch"
    """An operation does not apply to its operands."""

    MISSING_FIELD = "missing_field"
    """A record has no field of that name."""

    INDEX_OUT_OF_RANGE = "index_out_of_range"
    """An index is outside a sequence."""

    DIVISION_BY_ZERO = "division_by_zero"
    """A division or modulo by zero."""

    VALUE_TOO_LARGE = "value_too_large"
    """A string, sequence or number exceeds its size limit."""

    STEP_LIMIT = "step_limit"
    """The plan ran more evaluation steps than the limit."""

    TOOL_FAILED = "tool_failed"
    """A tool raised or returned something that is not a labeled value."""


class PlanError(Exception):
    """A plan was rejected or stopped.

    Attributes:
        code: what went wrong.
        line: the plan line, when known.
        detail: a name taken from the plan text itself, never from data.
    """

    def __init__(self, code: PlanErrorCode, line: int | None = None, detail: str = "") -> None:
        self.code = code
        self.line = line
        self.detail = detail
        where = f" at line {line}" if line is not None else ""
        what = f" ({detail})" if detail else ""
        super().__init__(f"{code.value}{where}{what}")
