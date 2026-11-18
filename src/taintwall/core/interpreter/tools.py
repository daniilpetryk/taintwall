# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Tools a plan may call, and how a call is labeled.

A tool result is labeled with the tool's own source (stamped here, never taken
on the tool's word), the label the tool returned, and the deep labels of its
arguments: if attacker data chose which file to read, the content depends on
the attacker too. Any tool failure becomes ``TOOL_FAILED`` without its text.
"""

import ast
import contextlib
from collections.abc import Callable, Iterable, Mapping

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.arguments import Arguments
from taintwall.core.interpreter.builtins import BUILTINS
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.operators import Result
from taintwall.core.interpreter.walk import deep_label
from taintwall.core.labels import Label, Labeled, Source, join_all

type Tool = Callable[..., Labeled[object]]
"""A callable the plan can invoke by name.

It takes ``Labeled`` arguments in any fixed signature and returns a labeled
value. A call with the wrong arguments fails as ``TOOL_FAILED``.
"""


class ToolNameClashError(ValueError):
    """A tool would shadow a builtin of the plan language."""

    def __init__(self, names: Iterable[str]) -> None:
        super().__init__(f"tool names clash with builtins: {', '.join(sorted(names))}")


class Toolbox:
    """The tools of one interpreter, each with the source stamped on its results."""

    def __init__(self, tools: Mapping[str, Tool]) -> None:
        clashes = set(tools) & BUILTINS
        if clashes:
            raise ToolNameClashError(clashes)
        # Validates tool names as sources up front (InvalidSourceError).
        self._tools = {name: (tool, Label.of(Source.tool(name))) for name, tool in tools.items()}

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def call(self, node: ast.Call, name: str, arguments: Arguments, guard: Guard) -> Result:
        """Call tool ``name``; it must exist (``name in toolbox``)."""
        tool, stamp = self._tools[name]
        result: object = None
        # Any tool failure becomes one typed code, raised below outside the
        # handler: the tool's exception text may carry an injection and must
        # not ride along as ``__cause__`` or ``__context__`` (decision Q3).
        with contextlib.suppress(Exception, SystemExit):
            result = tool(*arguments.positional, **arguments.keyword)
        if not isinstance(result, Labeled):
            raise error_at(PlanErrorCode.TOOL_FAILED, node, name)
        # The stamp means a tool cannot launder data by returning it under a
        # trusted label it received.
        inputs = (deep_label(item, guard, node) for item in arguments.all)
        return result.value, join_all(stamp, result.label, *inputs)
