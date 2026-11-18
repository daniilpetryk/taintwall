# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Where in the plan something happened, for typed errors and the trace."""

import ast

from taintwall.core.errors import PlanError, PlanErrorCode


def line_of(node: ast.AST) -> int | None:
    """The plan line of ``node``, or ``None`` if it has none."""
    line = getattr(node, "lineno", None)
    return line if type(line) is int else None


def error_at(code: PlanErrorCode, node: ast.AST, detail: str = "") -> PlanError:
    """A ``PlanError`` at ``node``'s line. ``detail`` must be plan text, never data."""
    return PlanError(code, line_of(node), detail)
