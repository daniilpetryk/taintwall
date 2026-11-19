# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Resource limits of one plan run, and the guard that enforces them.

Every check raises a typed ``PlanError`` before memory or time is spent:
sizes are computed before a value is built, and reading nested values draws
from a per-run budget.
"""

import ast
import dataclasses

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter.location import line_of
from taintwall.core.values import Record


class InvalidLimitError(ValueError):
    """A limit is not a positive integer."""

    def __init__(self, name: str) -> None:
        super().__init__(f"limit {name} must be a positive integer")


@dataclasses.dataclass(frozen=True, slots=True)
class Limits:
    """Bounds on what a single plan run may consume."""

    max_steps: int = 10_000
    """Evaluated expressions per run."""

    max_string: int = 100_000
    """Characters in one string."""

    max_items: int = 10_000
    """Elements in one sequence or fields in one record."""

    max_int_bits: int = 512
    """Bits in one integer: twice uint256, room for one multiplication."""

    max_visits: int = 1_000_000
    """Nested elements read per run (comparisons, labels, builtins).

    Repetition shares references, so ``(t,) * 1000`` is cheap to build but
    has a million elements to read; this budget bounds the reading.
    """

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise InvalidLimitError(field.name)


class Guard:
    """Counts the steps and reads of one run and checks value sizes.

    A limit error reports the line of the statement being run (``at``), so
    the helpers that read values need no syntax node of their own.
    """

    def __init__(self, limits: Limits) -> None:
        self._limits = limits
        self.steps = 0
        self._visits = 0
        # Set by ``at`` before any statement runs, so the start value is never read.
        self._line: int | None = None  # pragma: no mutate

    def at(self, statement: ast.stmt) -> None:
        """Start counting for ``statement``: later limit errors point at it."""
        self._line = line_of(statement)

    def fail(self, code: PlanErrorCode) -> PlanError:
        """A ``PlanError`` with ``code`` at the current statement."""
        return PlanError(code, self._line)

    def step(self) -> None:
        """Count one evaluated expression."""
        self.steps += 1
        if self.steps > self._limits.max_steps:
            raise self.fail(PlanErrorCode.STEP_LIMIT)

    def visit(self) -> None:
        """Count one nested element read."""
        self._visits += 1
        if self._visits > self._limits.max_visits:
            raise self.fail(PlanErrorCode.STEP_LIMIT)

    def check_string_length(self, length: int) -> None:
        """Reject a string length before the string is built."""
        if length > self._limits.max_string:
            raise self.fail(PlanErrorCode.VALUE_TOO_LARGE)

    def check_repeat(self, sequence: str | tuple[object, ...], count: int) -> None:
        """Reject ``sequence * count`` before it is built (a count below 1 is empty)."""
        limit = self._limits.max_string if type(sequence) is str else self._limits.max_items
        if len(sequence) * count > limit:
            raise self.fail(PlanErrorCode.VALUE_TOO_LARGE)

    def check_items(self, count: int) -> None:
        """Reject a sequence of ``count`` elements before it is built."""
        if count > self._limits.max_items:
            raise self.fail(PlanErrorCode.VALUE_TOO_LARGE)

    def check_size(self, value: object) -> None:
        """Reject a built value that is over a size limit."""
        limits = self._limits
        too_large = (
            (type(value) is str and len(value) > limits.max_string)
            or (type(value) in {tuple, Record} and len(value) > limits.max_items)  # type: ignore[arg-type]
            or (type(value) is int and value.bit_length() > limits.max_int_bits)
        )
        if too_large:
            raise self.fail(PlanErrorCode.VALUE_TOO_LARGE)
