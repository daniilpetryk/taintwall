# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""What a tool is, as the runtime and the planner know it.

A ``ToolSpec`` comes from trusted configuration only. Its description reaches
the planner, so it must never be taken from a tool server: an MCP server's
own description is attacker-reachable text (tool poisoning). Effects say what
a call may change, for the replanning rule (no replanning after a side
effect, Q3) and later for policies; ``readers`` say who may see its results
(Q9).
"""

import dataclasses
from collections.abc import Mapping
from typing import Final

from taintwall.core.labels import Readers
from taintwall.tools.schema import ValueType, accepts, render

_REQUIRED: Final = object()
"""Marker: a parameter without a default."""


@dataclasses.dataclass(frozen=True, slots=True)
class Param:
    """One parameter: its name, type, and default if it is optional."""

    name: str
    type: ValueType
    default: object = _REQUIRED

    @property
    def required(self) -> bool:
        """Whether a call must pass it."""
        return self.default is _REQUIRED


@dataclasses.dataclass(frozen=True, slots=True)
class Effects:
    """What a call may change; a call with no effect only reads."""

    writes: bool = False
    """Changes state: a file, the user's settings, a scheduled payment."""

    moves_funds: bool = False
    """Sends, schedules or approves a transfer of value."""

    outward: bool = False
    """Sends data outside: a message, an HTTP request, calldata (Q9)."""

    @property
    def read_only(self) -> bool:
        """Whether the call changes nothing."""
        return not (self.writes or self.moves_funds or self.outward)


READ_ONLY: Final = Effects()


class ArgumentError(ValueError):
    """A call's arguments do not fit the tool's parameters."""

    def __init__(self, tool: str) -> None:
        super().__init__(f"arguments do not fit {tool}")


@dataclasses.dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool's name, trusted description, parameters, result, effects and readers."""

    name: str
    description: str
    params: tuple[Param, ...]
    returns: ValueType
    effects: Effects
    """Required: a forgotten effect would let a plan be retried after a side effect (Q3)."""
    readers: Readers = Readers.USER
    """Private by default (Q9); results anyone may see opt into ``PUBLIC``."""

    def bind(
        self, positional: tuple[object, ...], keyword: Mapping[str, object]
    ) -> dict[str, object]:
        """The arguments the call gives, by parameter name, with their types checked.

        Defaults are not filled in: they are the implementation's, and the
        spec only shows them in the signature.

        Raises:
            ArgumentError: too many, missing, unknown or ill-typed arguments.
        """
        if len(positional) > len(self.params):
            raise ArgumentError(self.name)
        names = (param.name for param in self.params)
        # Never more values than names (checked above); strict would only fail on fewer.
        bound = dict(zip(names, positional, strict=False))  # pragma: no mutate - see above
        for name, value in keyword.items():
            if name in bound or name not in {param.name for param in self.params}:
                raise ArgumentError(self.name)
            bound[name] = value
        for param in self.params:
            if param.name not in bound:
                if param.required:
                    raise ArgumentError(self.name)
            elif not accepts(param.type, bound[param.name]):
                raise ArgumentError(self.name)
        return bound

    def signature(self) -> str:
        """The tool as a planner reads it: a Python signature and its description."""
        params = ", ".join(_param(param) for param in self.params)
        return f'def {self.name}({params}) -> {render(self.returns)}:\n    """{self.description}"""'


def _param(param: Param) -> str:
    text = f"{param.name}: {render(param.type)}"
    return text if param.required else f"{text} = {param.default!r}"
