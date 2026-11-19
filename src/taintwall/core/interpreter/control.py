# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Implicit flows: what control flow reveals, and the pc label that tracks it.

A branch reveals its condition: after ``if secret: x = 1``, the value of
``x`` tells whether ``secret`` held. The pc (program counter) label is the
join of every condition the current statement depends on. It is always
tracked and recorded on trace events; ``ImplicitFlows`` decides whether it
also enters values (decision Q2):

* ``STRICT``: values created under a condition carry it; after an ``if`` or
  a loop, every variable assigned anywhere inside it (even in a branch not
  taken) carries the condition; ``if c: break`` puts ``c`` on the rest of
  the loop; and a failure carries the pc (the termination channel).
* ``LENIENT``: values carry data dependencies only, like CaMeL's normal mode.

A condition reads only the container label of its value, like ``not``:
truthiness of a tuple or record depends on its size, not its elements.
"""

import ast
import contextlib
import dataclasses
import enum
from collections.abc import Iterable, Iterator
from typing import Self

from taintwall.core.errors import PlanError
from taintwall.core.labels import Label


class ImplicitFlows(enum.StrEnum):
    """Whether control dependencies enter values (decision Q2)."""

    STRICT = "strict"
    """Values, variables assigned in a block, and failures carry the pc."""

    LENIENT = "lenient"
    """Values carry data dependencies only; the pc is kept in the trace."""


class Signal(enum.Enum):
    """How a block of statements ended."""

    NEXT = enum.auto()
    BREAK = enum.auto()
    CONTINUE = enum.auto()


class LabeledPlanError(PlanError):
    """A plan failed at run time; ``label`` is what the failure depends on.

    Whether a failure happens can depend on data (a division by an untrusted
    zero) or on a branch taken (the termination channel), so a replanning
    loop must treat the failure itself as labeled data.
    """

    def __init__(self, error: PlanError, label: Label) -> None:
        super().__init__(error.code, error.line, error.detail)
        self.label = label

    @classmethod
    def of(cls, error: PlanError, label: Label) -> Self:
        """``error`` with ``label`` joined to any label it already carries."""
        if isinstance(error, LabeledPlanError):
            label = label.join(error.label)
        return cls(error, label)


@dataclasses.dataclass(slots=True)
class Dependence:
    """Conditions a block depends on: their joined label and their trace ids.

    The ids are the conditions that contributed to the label, for the trace.
    """

    label: Label | None = None
    ids: tuple[int, ...] = ()

    @classmethod
    def on(cls, label: Label, value_id: int) -> Self:
        """Depend on one condition: a value and its trace id."""
        return cls(label, (value_id,))

    def extend(self, other: Self) -> None:
        """Also depend on the conditions of ``other``."""
        merged = self.merged(other)
        self.label, self.ids = merged.label, merged.ids

    def merged(self, other: Self) -> Self:
        """Depend on the conditions of both.

        Only conditions that add to the label are listed, so a loop that
        evaluates its test thousands of times keeps a short list.
        """
        if other.label is None or (self.label is not None and self.label.covers(other.label)):
            return type(self)(self.label, self.ids)
        ids = tuple(dict.fromkeys((*self.ids, *other.ids)))
        label = other.label if self.label is None else self.label.join(other.label)
        return type(self)(label, ids)


class Flow:
    """The pc of one run: conditions the current statement depends on.

    A loop keeps one ``Dependence`` for its whole run: the iterated value or
    every evaluated ``while`` test, and the test of every ``if`` that can
    leave the loop early, since later iterations run only if it was false.
    An ``if`` around such an ``if`` can leave the loop too, so after any
    block the pc it restores is extended again by ``exit`` where needed.
    """

    def __init__(self, mode: ImplicitFlows) -> None:
        self.strict = mode is ImplicitFlows.STRICT
        self._pc = Dependence()
        self._history: Label | None = None
        self._loops: list[Dependence] = []
        self._absent: dict[str, Label] = {}
        self._exits: dict[ast.If, bool] = {}
        self._assigned: dict[ast.stmt, tuple[str, ...]] = {}

    @property
    def pc(self) -> Dependence:
        """Conditions the current statement depends on."""
        return self._pc

    @pc.setter
    def pc(self, pc: Dependence) -> None:
        self._pc = pc
        if pc.label is not None:
            known = self._history
            self._history = pc.label if known is None else known.join(pc.label)

    @property
    def history(self) -> Label | None:
        """Every condition the run has depended on so far.

        How many steps a run used depends on every branch it took (and on
        the data it evaluated), so a failure on the shared budget
        (``STEP_LIMIT``) depends on all of them, not only on the pc of the
        statement that hit it.
        """
        return self._history

    def value_label(self, label: Label) -> Label:
        """The label a value created now gets, or a failure that depends on ``label``."""
        return label.join(self.pc.label) if self.strict and self.pc.label else label

    @contextlib.contextmanager
    def branch(self, condition: Dependence) -> Iterator[None]:
        """Run a block that runs only because of ``condition``."""
        outer = self.pc
        self.pc = outer.merged(condition)
        yield
        self.pc = outer

    @contextlib.contextmanager
    def loop(self, frame: Dependence) -> Iterator[None]:
        """Run a loop whose iterations depend on ``frame``, which may grow."""
        outer = self.pc
        self._loops.append(frame)
        self.pc = outer.merged(frame)
        yield
        self._loops.pop()
        self.pc = outer

    def grow(self) -> None:
        """The innermost loop's ``frame`` changed: the rest of it depends on it."""
        self.pc = self.pc.merged(self._loops[-1])

    def exit(self, condition: Dependence) -> None:
        """An ``if`` that can leave the innermost loop has run."""
        self._loops[-1].extend(condition)
        self.grow()

    def absent(self, name: str) -> Label | None:
        """What the absence of variable ``name`` reveals, if it was skipped."""
        return self._absent.get(name)

    def skipped(self, name: str, label: Label) -> None:
        """A block that may have assigned ``name`` did not: its absence is labeled."""
        known = self._absent.get(name)
        if known is None or not known.covers(label):
            self._absent[name] = label if known is None else known.join(label)

    def leaves_loop(self, node: ast.If) -> bool:
        """Whether ``node`` holds a ``break`` or ``continue`` of the enclosing loop."""
        if node not in self._exits:
            self._exits[node] = exits_loop((*node.body, *node.orelse))
        return self._exits[node]

    def assigned(self, node: ast.stmt) -> tuple[str, ...]:
        """Variables ``node`` may assign, sorted, computed once per node."""
        if node not in self._assigned:
            self._assigned[node] = tuple(sorted(assigned_names(node)))
        return self._assigned[node]


def assigned_names(node: ast.stmt) -> frozenset[str]:
    """Variables a statement may assign, in any branch or iteration."""
    return frozenset(
        inner.id
        for inner in ast.walk(node)
        if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Store)
    )


def exits_loop(statements: Iterable[ast.stmt]) -> bool:
    """Whether ``break`` or ``continue`` in ``statements`` leaves the enclosing loop.

    One inside a nested loop belongs to that loop and does not count.
    """
    pending = list(statements)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Break | ast.Continue):
            return True
        if isinstance(node, ast.If):
            pending.extend((*node.body, *node.orelse))
    return False
