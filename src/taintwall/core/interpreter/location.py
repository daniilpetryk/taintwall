# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Where in the plan something happened, for typed errors and the trace."""

import ast

from taintwall.core.errors import PlanError, PlanErrorCode

type Located = ast.stmt | ast.expr
"""A syntax node with a line: statements and expressions always have one."""


def line_of(node: Located) -> int:
    """The plan line of ``node``."""
    return node.lineno


def error_at(code: PlanErrorCode, node: Located, detail: str = "") -> PlanError:
    """A ``PlanError`` at ``node``'s line. ``detail`` must be plan text, never data."""
    return PlanError(code, line_of(node), detail)
