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
import dataclasses
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter.arguments import Arguments
from taintwall.core.interpreter.builtins import BUILTINS
from taintwall.core.interpreter.control import LabeledPlanError
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at, line_of
from taintwall.core.interpreter.trace import Result
from taintwall.core.interpreter.walk import deep_label
from taintwall.core.labels import Label, Labeled, Readers, Source, join_all

type Tool = Callable[..., Labeled[object]]
"""A callable the plan can invoke by name.

It takes ``Labeled`` arguments in any fixed signature and returns a labeled
value. A call with the wrong arguments fails as ``TOOL_FAILED``.
"""


def _call_isolated(tool: Tool, arguments: Arguments) -> object:
    """The tool's result, or ``None`` if it raised.

    The caller raises ``TOOL_FAILED`` outside any handler: the tool's
    exception text may carry an injection and must not ride along as
    ``__cause__`` or ``__context__`` (decision Q3).
    """
    with contextlib.suppress(Exception, SystemExit):
        return tool(*arguments.positional, **arguments.keyword)
    return None  # pragma: no mutate - any non-Labeled result is TOOL_FAILED


def _hook_isolated(hook: "CallHook", call: "PendingCall") -> bool:
    """Run the hook; ``False`` if it crashed with anything but a ``PlanError``.

    A crash's text may carry attacker data the hook was inspecting, so the
    caller fails the call as ``TOOL_FAILED`` outside any handler, as for a
    tool (Q3); a ``PlanError`` is the hook's typed refusal and propagates.
    """
    try:
        hook.before(call)
    except PlanError:
        raise
    except Exception:  # noqa: BLE001 - any crash is a refused call, its text dropped
        return False
    return True


@dataclasses.dataclass(frozen=True, slots=True)
class PendingCall:
    """A tool call about to run: what a hook sees before any side effect.

    ``labels`` are the arguments' deep labels, positional first, in the order
    of ``arguments.all``; ``control`` is the pc, the conditions that decided
    the call runs at all (``None`` outside any branch).
    """

    name: str
    arguments: Arguments
    labels: tuple[Label, ...]
    control: Label | None
    line: int


class CallHook(Protocol):
    """Runs before every tool call, after its arguments' labels are read.

    It may raise ``PlanError`` to stop the call before its side effect (a
    wrong argument type, later a policy denial); the error is labeled like
    any failure of the statement.
    """

    def before(self, call: PendingCall) -> None:
        """Check, record or refuse ``call``."""
        ...


class Hooks:
    """Several hooks, run in the order given; the first refusal stops the call.

    Put hooks that refuse (policies) before hooks that record (the call log),
    so a refused call is never recorded as made.
    """

    def __init__(self, *hooks: CallHook) -> None:
        self._hooks = hooks

    def before(self, call: PendingCall) -> None:
        """Run every hook on ``call``, in order."""
        for hook in self._hooks:
            hook.before(call)


class ToolNameClashError(ValueError):
    """A tool would shadow a builtin of the plan language."""

    def __init__(self, names: Iterable[str]) -> None:
        super().__init__(f"tool names clash with builtins: {', '.join(sorted(names))}")


class UnknownReadersError(ValueError):
    """Readers are given for names that are not tools: a typo would leave a tool public."""

    def __init__(self, names: Iterable[str]) -> None:
        super().__init__(f"readers for unknown tools: {', '.join(sorted(names))}")


class Toolbox:
    """The tools of one interpreter, each with the source stamped on its results."""

    def __init__(
        self,
        tools: Mapping[str, Tool],
        hook: CallHook | None = None,
        readers: Mapping[str, Readers] | None = None,
    ) -> None:
        clashes = set(tools) & BUILTINS
        if clashes:
            raise ToolNameClashError(clashes)
        given = readers or {}
        unknown = set(given) - set(tools)
        if unknown:
            raise UnknownReadersError(unknown)
        # Validates tool names as sources and readers up front (InvalidSourceError,
        # InvalidReadersError). The stamp labels failures too: whether a private
        # tool failed is as private as its results.
        self._tools = {
            name: (tool, Label.of(Source.tool(name), readers=given.get(name, Readers.PUBLIC)))
            for name, tool in tools.items()
        }
        self._hook = hook

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def call(
        self,
        node: ast.Call,
        name: str,
        arguments: Arguments,
        guard: Guard,
        control: Label | None,
    ) -> Result:
        """Call tool ``name``; it must exist (``name in toolbox``).

        ``control`` is the pc; the hook, if any, sees it with the argument
        labels before the tool runs.
        """
        tool, stamp = self._tools[name]
        # Read before the call: a limit hit here must stop the plan before
        # the tool's side effect, and the hook checks these labels first.
        inputs = tuple(deep_label(item, guard) for item in arguments.all)
        pending = PendingCall(name, arguments, inputs, control, line_of(node))
        if self._hook is not None and not _hook_isolated(self._hook, pending):
            raise LabeledPlanError(error_at(PlanErrorCode.TOOL_FAILED, node, name), stamp)
        result = _call_isolated(tool, arguments)
        if not isinstance(result, Labeled):
            # Whether a tool fails depends on its environment, not only on
            # its arguments, so the failure carries the tool's source; the
            # evaluator joins the labels of the arguments it read.
            raise LabeledPlanError(error_at(PlanErrorCode.TOOL_FAILED, node, name), stamp)
        # The stamp means a tool cannot launder data by returning it under a
        # trusted label it received.
        return result.value, join_all(stamp, result.label, *inputs)
