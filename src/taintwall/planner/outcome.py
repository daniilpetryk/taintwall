# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""How a request ended: the programs tried, what the user was told, what it cost."""

import dataclasses
from typing import Literal

from taintwall.core.errors import PlanError
from taintwall.core.interpreter import RunResult
from taintwall.core.labels import Label
from taintwall.llm import LLMError, Usage
from taintwall.tools import CallRecord

type Phase = Literal["parse", "check", "run"]
type Failure = Literal["static", "runtime", "effects", "policy", "planner"]


@dataclasses.dataclass(frozen=True, slots=True)
class Attempt:
    """One program the planner wrote, and why it was not the last."""

    source: str
    phase: Phase
    errors: tuple[PlanError, ...] = ()
    label: Label | None = None
    """For a runtime error: what it depends on (data the planner never saw)."""


@dataclasses.dataclass(frozen=True, slots=True)
class Answer:
    """What the user was told, and the label of what it was built from."""

    text: str
    label: Label


@dataclasses.dataclass(frozen=True, slots=True)
class Outcome:
    """How a request ended."""

    attempts: tuple[Attempt, ...]
    answers: tuple[Answer, ...]
    result: RunResult | None
    """The last run's variables and trace, if a program ran to the end."""
    failure: Failure | None
    """``None`` when a program ran to the end."""
    planner_error: LLMError | None
    """Why the planner's model gave no program, when ``failure`` is ``"planner"``."""
    effects_happened: bool
    calls: tuple[CallRecord, ...]
    """Every tool call of every attempt, in order: the shared call log's records."""
    usage: Usage

    @property
    def completed(self) -> bool:
        """Whether a program ran to the end, answering or not."""
        return self.failure is None

    @property
    def answered(self) -> bool:
        """Whether a program ran to the end and told the user something."""
        return self.completed and bool(self.answers)
