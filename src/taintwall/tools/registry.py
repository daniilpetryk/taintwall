# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The tool registry: specs from trusted configuration, and their implementations.

``tools()`` gives the interpreter one wrapper per tool: it binds the plan's
arguments to the tool's parameters as plain Python, calls the
implementation, and labels the result with the tool's source and readers
(``values.to_labeled``). ``call_log()`` gives a fresh hook for one run.
Implementations are ordinary Python callables taking keyword arguments, so a
benchmark's functions or an MCP client can stand behind a spec unchanged.
"""

from collections.abc import Callable, Iterable, Mapping

from taintwall.core.interpreter import Tool
from taintwall.core.labels import Label, Labeled, Source
from taintwall.tools.calls import CallLog
from taintwall.tools.schema import accepts, records_in, render_record
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


class Registry:
    """Tools a planner may use: each spec with what implements it."""

    def __init__(self, entries: Iterable[tuple[ToolSpec, Implementation]]) -> None:
        self._entries: dict[str, tuple[ToolSpec, Implementation]] = {}
        for spec, implementation in entries:
            if spec.name in self._entries:
                raise DuplicateToolError(spec.name)
            self._entries[spec.name] = (spec, implementation)

    @property
    def specs(self) -> Mapping[str, ToolSpec]:
        """Every tool's spec by name, in the order given."""
        return {name: spec for name, (spec, _) in self._entries.items()}

    def tools(self) -> Mapping[str, Tool]:
        """One interpreter tool per spec."""
        return {name: _wrap(spec, run) for name, (spec, run) in self._entries.items()}

    def describe(self) -> str:
        """Every record type once, then every tool's signature: what a planner reads."""
        records = {
            record.name: record
            for spec in self.specs.values()
            for kind in (*(param.type for param in spec.params), spec.returns)
            for record in records_in(kind)
        }
        classes = (render_record(record) for record in records.values())
        signatures = (spec.signature() for spec in self.specs.values())
        return "\n\n".join((*classes, *signatures))

    def call_log(self) -> CallLog:
        """A fresh hook that checks and records the calls of one run."""
        return CallLog(self.specs)


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
