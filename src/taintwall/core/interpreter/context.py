# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""What a syntax-node handler may use from the running evaluator.

Handlers (``reads``, ``literals``, ``operations``) depend on this protocol,
not on the evaluator, so the evaluator can import them for its dispatch table
without an import cycle, and each handler sees only what it needs.
"""

import ast
from typing import Protocol

from taintwall.core.interpreter.arguments import Arguments
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.trace import Origin, Ref, Result
from taintwall.core.labels import Label


class ToolCalls(Protocol):
    """What a handler needs from the tools: whether a name is one, and a call."""

    def __contains__(self, name: object) -> bool:
        """Whether ``name`` is a tool of this run."""
        ...

    def call(self, node: ast.Call, name: str, arguments: Arguments, guard: Guard) -> Result:
        """Call tool ``name`` and label its result."""
        ...


class Context(Protocol):
    """The state of one run, as seen by a node handler."""

    @property
    def guard(self) -> Guard:
        """Limits of this run."""
        ...

    @property
    def literal_label(self) -> Label:
        """Label of every plan literal: the plan and the requester."""
        ...

    @property
    def tools(self) -> ToolCalls:
        """Tools the plan may call."""
        ...

    def expr(self, node: ast.expr) -> Ref:
        """Evaluate a sub-expression."""
        ...

    def new(self, result: Result, origin: Origin) -> Ref:
        """Make ``result`` a labeled value and record it in the trace."""
        ...

    def variable(self, name: str) -> Ref | None:
        """The value of variable ``name``, if it is assigned."""
        ...
