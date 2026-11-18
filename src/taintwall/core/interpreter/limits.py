# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Resource limits of one plan run, and the guard that enforces them.

Every check raises a typed ``PlanError`` before memory or time is spent:
sizes are computed before a value is built, and reading nested values draws
from a per-run budget.
"""

import ast
import dataclasses

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.location import error_at
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
    """Counts the steps and reads of one run and checks value sizes."""

    def __init__(self, limits: Limits) -> None:
        self._limits = limits
        self.steps = 0
        self._visits = 0

    def step(self, node: ast.AST) -> None:
        """Count one evaluated expression."""
        self.steps += 1
        if self.steps > self._limits.max_steps:
            raise error_at(PlanErrorCode.STEP_LIMIT, node)

    def visit(self, node: ast.AST) -> None:
        """Count one nested element read."""
        self._visits += 1
        if self._visits > self._limits.max_visits:
            raise error_at(PlanErrorCode.STEP_LIMIT, node)

    def check_string_length(self, length: int, node: ast.AST) -> None:
        """Reject a string length before the string is built."""
        if length > self._limits.max_string:
            raise error_at(PlanErrorCode.VALUE_TOO_LARGE, node)

    def check_repeat(self, sequence: str | tuple[object, ...], count: int, node: ast.AST) -> None:
        """Reject ``sequence * count`` before it is built."""
        is_str = type(sequence) is str
        limit = self._limits.max_string if is_str else self._limits.max_items
        if len(sequence) * max(count, 0) > limit:
            raise error_at(PlanErrorCode.VALUE_TOO_LARGE, node)

    def check_size(self, value: object, node: ast.AST) -> None:
        """Reject a built value that is over a size limit."""
        limits = self._limits
        too_large = (
            (type(value) is str and len(value) > limits.max_string)
            or (type(value) is tuple and len(value) > limits.max_items)
            or (type(value) is int and value.bit_length() > limits.max_int_bits)
            or (isinstance(value, Record) and len(value.fields) > limits.max_items)
        )
        if too_large:
            raise error_at(PlanErrorCode.VALUE_TOO_LARGE, node)
