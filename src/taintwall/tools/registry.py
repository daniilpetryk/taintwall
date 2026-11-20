# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The tool registry: specs from trusted configuration, and their implementations.

``interpreter()`` is the one way to run plans over the tools: it wires one
wrapper per tool, their readers, any policies and a fresh call log. A wrapper
binds the plan's arguments to the tool's parameters as plain Python, calls
the implementation, and labels the result with the tool's source and readers
(``values.to_labeled``).
Implementations are ordinary Python callables taking keyword arguments, so a
benchmark's functions or an MCP client can stand behind a spec unchanged.
"""

from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Unpack

from taintwall.core.interpreter import CallHook, Hooks, Interpreter, InterpreterOptions, Tool
from taintwall.core.labels import Label, Labeled, Readers, Source
from taintwall.tools.calls import CallLog
from taintwall.tools.schema import RecordOf, accepts, records_in, render_record
from taintwall.tools.spec import ToolSpec
from taintwall.tools.values import to_labeled, to_python

type Implementation = Callable[..., object]
"""What runs a tool: keyword arguments of plain Python values, any result."""


class ResultTypeError(TypeError):
    """A tool returned a value of another type than its spec promises."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} returned a value of the wrong type")


class DuplicateToolError(ValueError):
    """Two tools share a name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"duplicate tool: {name}")


class RecordClashError(ValueError):
    """Two different record types share a name: a planner could not tell them apart."""

    def __init__(self, name: str) -> None:
        super().__init__(f"two record types named {name}")


class Registry:
    """Tools a planner may use: each spec with what implements it."""

    def __init__(self, entries: Iterable[tuple[ToolSpec, Implementation]]) -> None:
        """Collect the tools.

        Raises:
            DuplicateToolError: two tools share a name.
            RecordClashError: two different record types share a name.
        """
        self._entries: dict[str, tuple[ToolSpec, Implementation]] = {}
        for spec, implementation in entries:
            if spec.name in self._entries:
                raise DuplicateToolError(spec.name)
            self._entries[spec.name] = (spec, implementation)
        self._specs = MappingProxyType({name: spec for name, (spec, _) in self._entries.items()})
        self._records = _records(self._specs.values())
        self._readers = MappingProxyType({name: spec.readers for name, spec in self._specs.items()})

    @property
    def specs(self) -> Mapping[str, ToolSpec]:
        """Every tool's spec by name, in the order given."""
        return self._specs

    @property
    def readers(self) -> Mapping[str, Readers]:
        """Who may see each tool's results and failures: the interpreter's ``readers``."""
        return self._readers

    def _tools(self) -> Mapping[str, Tool]:
        """One interpreter tool per spec."""
        return {name: _wrap(spec, run) for name, (spec, run) in self._entries.items()}

    def describe(self) -> str:
        """Every record type once, then every tool's signature: what a planner reads."""
        classes = (render_record(record) for record in self._records.values())
        signatures = (spec.signature() for spec in self._specs.values())
        return "\n\n".join((*classes, *signatures))

    def interpreter(
        self, *, policies: Iterable[CallHook] = (), **options: Unpack[InterpreterOptions]
    ) -> tuple[Interpreter, CallLog]:
        """An interpreter over these tools, wired as they must be, and its call log.

        Tools, their readers and the call log go in together, so none can be
        forgotten. ``policies`` run before the log: a refused call is never
        recorded as made. ``options`` are the interpreter's own (requester,
        mint, limits, flows).
        """
        log = CallLog(self._specs)
        interpreter = Interpreter(
            self._tools(), hook=Hooks(*policies, log), readers=self.readers, **options
        )
        return interpreter, log


def _records(specs: Iterable[ToolSpec]) -> dict[str, RecordOf]:
    """Every record type of these specs by name, inner ones first.

    Raises:
        RecordClashError: two different record types share a name.
    """
    records: dict[str, RecordOf] = {}
    kinds = (kind for spec in specs for kind in (*(p.type for p in spec.params), spec.returns))
    for record in (record for kind in kinds for record in records_in(kind)):
        if records.setdefault(record.name, record) != record:
            raise RecordClashError(record.name)
    return records


def _wrap(spec: ToolSpec, run: Implementation) -> Tool:
    """The interpreter's view of a tool: labeled values in, a labeled value out."""
    label = Label.of(Source.tool(spec.name), readers=spec.readers)

    def tool(*positional: Labeled[object], **keyword: Labeled[object]) -> Labeled[object]:
        arguments = spec.bind(
            tuple(to_python(item) for item in positional),
            {name: to_python(item) for name, item in keyword.items()},
        )
        result = to_labeled(run(**arguments), label)
        # The planner was promised spec.returns; anything else is a failed call.
        if not accepts(spec.returns, to_python(result)):
            raise ResultTypeError(spec.name)
        return result

    return tool
