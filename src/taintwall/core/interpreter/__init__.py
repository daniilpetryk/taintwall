# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Plan interpreter: runs a checked plan over labeled values.

Modules, from the entry point down (``pyproject.toml`` enforces the order):

* ``runtime``: ``Interpreter``, one requester and one set of tools;
* ``evaluator``: holds one run's state and dispatches each syntax node;
* ``reads``, ``literals``, ``operations``: one handler per syntax node; they
  depend on the ``context`` protocol, not on the evaluator;
* ``tools`` and ``builtins``: what a call does and how it is labeled;
* ``operators``: type rules, values and labels of operators;
* ``walk``: reading nested values (plain values, deep labels, depth);
* ``limits``, ``trace``, ``arguments``, ``location``: run limits, the
  provenance trace, call arguments and error locations.

Failures raise ``PlanError`` with a typed code and the plan line only
(decision Q3). Policies are not checked here; that is stage 3.
"""

from taintwall.core.interpreter.builtins import BUILTINS
from taintwall.core.interpreter.evaluator import RunResult
from taintwall.core.interpreter.limits import InvalidLimitError, Limits
from taintwall.core.interpreter.runtime import Interpreter, InvalidRequesterError
from taintwall.core.interpreter.tools import Tool, ToolNameClashError
from taintwall.core.interpreter.trace import EventKind, TraceEvent

__all__ = [
    "BUILTINS",
    "EventKind",
    "Interpreter",
    "InvalidLimitError",
    "InvalidRequesterError",
    "Limits",
    "RunResult",
    "Tool",
    "ToolNameClashError",
    "TraceEvent",
]
