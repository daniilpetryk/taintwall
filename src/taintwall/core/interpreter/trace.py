# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Provenance trace: which value was derived from which.

Every value the interpreter creates gets an id and one event that lists the
ids it was computed from. The provenance viewer (stage 6) renders it. Events
hold plan text (operator, tool, field or variable names), never data.
"""

import ast
import dataclasses
import enum
from collections.abc import Iterator
from typing import NamedTuple

from taintwall.core.interpreter.location import Located, line_of
from taintwall.core.labels import Label, Labeled


class EventKind(enum.StrEnum):
    """What produced a value in the trace."""

    LITERAL = "literal"
    OPERATION = "operation"
    CALL = "call"
    FIELD = "field"
    INDEX = "index"
    BUILD = "build"
    ASSIGN = "assign"
    CONTROL = "control"
    """A variable now also depends on a condition (strict implicit flows)."""


@dataclasses.dataclass(frozen=True, slots=True)
class TraceEvent:
    """One step of the provenance trace.

    ``detail`` comes from the plan text (an operator, tool, field or variable
    name), never from data, so the trace can be shown to the planner.
    ``control`` is the pc when the event happened: the label of every
    condition it depends on, in both implicit-flow modes, or ``None``.
    """

    value_id: int
    kind: EventKind
    line: int | None
    detail: str
    inputs: tuple[int, ...]
    label: Label
    control: Label | None = None


type Result = tuple[object, Label]
"""A computed value and its label, before it becomes a ``Labeled`` value."""


class Ref(NamedTuple):
    """A value inside a run, with its trace id."""

    labeled: Labeled[object]
    id: int


class Origin(NamedTuple):
    """How a new value was made: the trace event, without the value itself."""

    kind: EventKind
    node: Located
    detail: str = ""
    inputs: tuple[int, ...] = ()


class Trace:
    """Append-only list of trace events for one run."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def add(self, labeled: Labeled[object], origin: Origin, control: Label | None) -> Ref:
        """Record a new value and give it the next id."""
        value_id = len(self._events)
        self._append(value_id, labeled.label, origin, control)
        return Ref(labeled, value_id)

    def assign(self, ref: Ref, name: str, node: ast.stmt, control: Label | None) -> None:
        """Record that variable ``name`` now holds ``ref``."""
        origin = Origin(EventKind.ASSIGN, node, name, (ref.id,))
        self._append(ref.id, ref.labeled.label, origin, control)

    def labels(self) -> Iterator[Label]:
        """The label of every event so far."""
        return (event.label for event in self._events)

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        """All events so far, in order."""
        return tuple(self._events)

    def _append(self, value_id: int, label: Label, origin: Origin, control: Label | None) -> None:
        line = line_of(origin.node)
        event = TraceEvent(
            value_id, origin.kind, line, origin.detail, origin.inputs, label, control
        )
        self._events.append(event)
