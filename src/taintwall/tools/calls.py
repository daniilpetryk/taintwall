# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The call log: the interpreter's hook before every tool call.

It checks a call's arguments against the tool's parameters before the tool
runs, so a wrong type is a typed error (``BAD_ARGUMENTS``, the tool's name as
detail) instead of a failure after a side effect. It records every call with
its pc and effects: the replanning loop (Q3) may retry a plan only while
``effects_happened`` is false, and stage 3's policies read the same records.
"""

import dataclasses
from collections.abc import Mapping

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter import PendingCall
from taintwall.core.labels import Label
from taintwall.tools.spec import ArgumentError, Effects, ToolSpec
from taintwall.tools.values import to_python


@dataclasses.dataclass(frozen=True, slots=True)
class CallRecord:
    """A tool call that passed the checks and ran (or was about to run)."""

    name: str
    line: int
    effects: Effects
    labels: tuple[tuple[str, Label], ...]
    """Each given argument's deep label, by parameter name, in call order."""
    control: Label | None
    """The pc: conditions that decided the call runs at all."""

    def label_of(self, name: str) -> Label | None:
        """The deep label of argument ``name``, or ``None`` if the call left it out."""
        return dict(self.labels).get(name)


class CallLog:
    """Checks and records the tool calls of one run; a ``CallHook``."""

    def __init__(self, specs: Mapping[str, ToolSpec]) -> None:
        self._specs = dict(specs)
        self._records: list[CallRecord] = []

    def before(self, call: PendingCall) -> None:
        """Refuse ill-typed arguments and tools without a spec; record the call.

        Raises:
            PlanError: ``BAD_ARGUMENTS`` with the tool's name, or
                ``UNKNOWN_TOOL`` for a tool the registry has no spec for
                (its effects are unknown, so it does not run).
        """
        spec = self._specs.get(call.name)
        if spec is None:
            raise PlanError(PlanErrorCode.UNKNOWN_TOOL, call.line, call.name)
        positional = tuple(to_python(item) for item in call.arguments.positional)
        keyword = {name: to_python(item) for name, item in call.arguments.keyword.items()}
        try:
            bound = spec.bind(positional, keyword)
        except ArgumentError:
            raise PlanError(PlanErrorCode.BAD_ARGUMENTS, call.line, call.name) from None
        # bind keeps call order: positional by parameter, then keywords as given,
        # the order of call.labels; one label per bound argument, so never ragged.
        labels = tuple(zip(bound, call.labels, strict=True))  # pragma: no mutate - see above
        self._records.append(CallRecord(call.name, call.line, spec.effects, labels, call.control))

    @property
    def specs(self) -> Mapping[str, ToolSpec]:
        """The specs this log checks calls against."""
        return self._specs

    @property
    def records(self) -> tuple[CallRecord, ...]:
        """Every call so far, in order."""
        return tuple(self._records)

    @property
    def effects_happened(self) -> bool:
        """Whether a call that changes something has been made: no replanning after it."""
        return any(not record.effects.read_only for record in self._records)
