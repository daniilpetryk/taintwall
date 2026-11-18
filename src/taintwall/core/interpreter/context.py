# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""What a syntax-node handler may use from the running evaluator.

Handlers (``reads``, ``literals``, ``operations``) depend on this protocol,
not on the evaluator, so the evaluator can import them for its dispatch table
without an import cycle, and each handler sees only what it needs.
"""

import ast
from typing import Protocol

from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.operators import Result
from taintwall.core.interpreter.tools import Toolbox
from taintwall.core.interpreter.trace import Origin, Ref
from taintwall.core.labels import Label


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
    def tools(self) -> Toolbox:
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
