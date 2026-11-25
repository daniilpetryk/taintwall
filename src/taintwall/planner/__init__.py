# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The planner (P-LLM) and the loop that checks, runs and retries its plans.

Modules, from the entry point down (``pyproject.toml`` enforces the order):

* ``loop``: ``Planner``: plan, check, run, retry within ``Replan`` (Q3);
* ``prompt | answer | outcome``: what the planner reads | the ``answer``
  tool | how a request ended.
"""

from taintwall.planner.answer import ReservedToolError, answer_tool
from taintwall.planner.loop import Planner, PlannerModel, Replan
from taintwall.planner.outcome import Answer, Attempt, Failure, Outcome, Phase
from taintwall.planner.prompt import PLAN_FIELD, PLAN_SCHEMA, PLAN_SCHEMA_NAME, SYSTEM, rules

__all__ = [
    "PLAN_FIELD",
    "PLAN_SCHEMA",
    "PLAN_SCHEMA_NAME",
    "SYSTEM",
    "Answer",
    "Attempt",
    "Failure",
    "Outcome",
    "Phase",
    "Planner",
    "PlannerModel",
    "Replan",
    "ReservedToolError",
    "answer_tool",
    "rules",
]
