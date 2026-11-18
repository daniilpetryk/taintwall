# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Plan interpreter: runs a checked plan over labeled values.

Every value is a ``Labeled`` value. Rules for explicit flows (stage 1, PR 2):

* a plan literal is labeled with the plan and the requester (the owner or
  another principal), so a plan written from a stranger's request carries
  that stranger as a source (contribution C);
* an operation's result is labeled with everything it read: its operands and,
  when it reads the contents of a sequence or record, every element;
* a tool result is labeled with the tool's own label joined with the labels
  of its arguments: if attacker data chose which file to read, the content
  depends on the attacker too;
* reading a field or an element joins the field's label with the container's
  (and the index's) label; ``len`` reads only the container's label;
* ``and``/``or`` short-circuit like Python, and the result carries the label
  of every operand that was evaluated, since the left one chose the outcome.

The interpreter records a trace of which value was derived from which, for
the provenance viewer. Failures raise ``PlanError`` with a code and the plan
line only (decision Q3). Policies are not checked here; that is stage 3.
"""

import ast
import contextlib
import dataclasses
import enum
import operator
import types
from collections.abc import Callable, Iterable, Mapping
from typing import Final

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.labels import (
    Label,
    Labeled,
    MutableValueError,
    NonFiniteNumberError,
    Source,
    SourceKind,
    ValueTooDeepError,
    join_all,
)
from taintwall.core.mint import Mint
from taintwall.core.plan import Plan
from taintwall.core.values import Record

type Tool = Callable[..., Labeled[object]]
"""A callable the plan can invoke by name.

It takes ``Labeled`` arguments in any fixed signature and returns a labeled
value. A call with the wrong arguments fails as ``TOOL_FAILED``.
"""


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


class EventKind(enum.StrEnum):
    """What produced a value in the trace."""

    LITERAL = "literal"
    OPERATION = "operation"
    CALL = "call"
    FIELD = "field"
    INDEX = "index"
    BUILD = "build"
    ASSIGN = "assign"


@dataclasses.dataclass(frozen=True, slots=True)
class TraceEvent:
    """One step of the provenance trace.

    ``detail`` comes from the plan text (an operator, tool, field or variable
    name), never from data, so the trace can be shown to the planner.
    """

    value_id: int
    kind: EventKind
    line: int
    detail: str
    inputs: tuple[int, ...]
    label: Label


@dataclasses.dataclass(frozen=True, slots=True)
class RunResult:
    """Variables after the run, the provenance trace, and steps used."""

    variables: Mapping[str, Labeled[object]]
    trace: tuple[TraceEvent, ...]
    steps: int


class InvalidRequesterError(ValueError):
    """The requester is not the owner or a precise principal."""

    def __init__(self) -> None:
        super().__init__("requester must be the owner or a precise principal source")


class ToolNameClashError(ValueError):
    """A tool would shadow a builtin of the plan language."""

    def __init__(self, names: Iterable[str]) -> None:
        super().__init__(f"tool names clash with builtins: {', '.join(sorted(names))}")


_Ref = tuple[Labeled[object], int]
"""A value with its trace id."""

_BINARY: Final[Mapping[type[ast.operator], Callable[..., object]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_UNARY: Final[Mapping[type[ast.unaryop], Callable[..., object]]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
}
_COMPARE: Final[Mapping[type[ast.cmpop], Callable[..., object]]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda left, right: operator.contains(right, left),
    ast.NotIn: lambda left, right: not operator.contains(right, left),
}
BUILTINS: Final = frozenset({"len", "abs", "min", "max", "sum"})
"""Functions every plan can call besides tools."""

_REQUESTER_KINDS: Final = frozenset({SourceKind.OWNER, SourceKind.PRINCIPAL})


class Interpreter:
    """Runs plans against a fixed set of tools on behalf of one requester."""

    def __init__(
        self,
        tools: Mapping[str, Tool],
        *,
        requester: Source,
        mint: Mint,
        limits: Limits | None = None,
    ) -> None:
        if requester.kind not in _REQUESTER_KINDS or requester.is_category:
            raise InvalidRequesterError
        clashes = set(tools) & BUILTINS
        if clashes:
            raise ToolNameClashError(clashes)
        self._tools = dict(tools)
        self._literal_label = Label.of(mint.plan(), requester)
        self._limits = limits or Limits()

    def run(self, plan: Plan) -> RunResult:
        """Run ``plan`` once from an empty environment.

        Raises:
            PlanError: with a typed code if the plan fails.
        """
        run = _Run(self._tools, self._literal_label, self._limits)
        for statement in plan.tree.body:
            run.statement(statement)
        return run.result()


def _plain(value: object) -> object:
    """The value with every nested label removed, for computing and comparing."""
    if isinstance(value, Labeled):
        return _plain(value.value)
    if type(value) is tuple:
        return tuple(_plain(item) for item in value)
    if isinstance(value, Record):
        return {name: _plain(field) for name, field in value.fields}
    return value


def _nested_labels(value: object) -> list[Label]:
    """Labels of every element nested inside ``value``."""
    labels: list[Label] = []
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Labeled):
            labels.append(item.label)
            pending.append(item.value)
        elif type(item) is tuple:
            pending.extend(item)
        elif isinstance(item, Record):
            pending.extend(field for _, field in item.fields)
    return labels


def _deep_label(ref: _Ref) -> Label:
    """The label of a value and of everything nested in it."""
    labeled, _ = ref
    return join_all(labeled.label, *_nested_labels(labeled.value))


def _line(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 0))


class _Run:
    """State of one plan run: variables, trace and step count."""

    def __init__(self, tools: Mapping[str, Tool], literal_label: Label, limits: Limits) -> None:
        self._tools = tools
        self._literal_label = literal_label
        self._limits = limits
        self._variables: dict[str, _Ref] = {}
        self._trace: list[TraceEvent] = []
        self._steps = 0

    def result(self) -> RunResult:
        variables = {name: ref[0] for name, ref in self._variables.items()}
        return RunResult(types.MappingProxyType(variables), tuple(self._trace), self._steps)

    # --- statements ---------------------------------------------------------

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            assert isinstance(target, ast.Name)  # noqa: S101 - parse_plan guarantees it
            value, value_id = self.expr(node.value)
            self._event(value_id, EventKind.ASSIGN, node, target.id, (value_id,), value.label)
            self._variables[target.id] = (value, value_id)
        elif isinstance(node, ast.Expr):
            self.expr(node.value)

    # --- expressions --------------------------------------------------------

    def expr(self, node: ast.expr) -> _Ref:
        self._steps += 1
        if self._steps > self._limits.max_steps:
            raise PlanError(PlanErrorCode.STEP_LIMIT, _line(node))
        handler = _HANDLERS[type(node)]
        return handler(self, node)

    def _constant(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.Constant)  # noqa: S101 - dispatch guarantees the type
        return self._new(node.value, self._literal_label, EventKind.LITERAL, node, "", ())

    def _name(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.Name)  # noqa: S101 - dispatch guarantees the type
        ref = self._variables.get(node.id)
        if ref is None:
            raise PlanError(PlanErrorCode.UNKNOWN_NAME, _line(node), node.id)
        return ref

    def _binary(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.BinOp)  # noqa: S101 - dispatch guarantees the type
        left, right = self.expr(node.left), self.expr(node.right)
        op_name = type(node.op).__name__
        if type(left[0].value) is tuple and type(right[0].value) is tuple and op_name == "Add":
            # Concatenation keeps per-element labels; the result's own label
            # is the two sequences' labels.
            value: object = left[0].value + right[0].value
            label = left[0].label.join(right[0].label)
        else:
            value = self._apply(
                node, _BINARY[type(node.op)], _plain(left[0].value), _plain(right[0].value)
            )
            label = _deep_label(left).join(_deep_label(right))
        return self._new(value, label, EventKind.OPERATION, node, op_name, (left[1], right[1]))

    def _unary(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.UnaryOp)  # noqa: S101 - dispatch guarantees the type
        operand = self.expr(node.operand)
        value = self._apply(node, _UNARY[type(node.op)], _plain(operand[0].value))
        name = type(node.op).__name__
        return self._new(
            value, _deep_label(operand), EventKind.OPERATION, node, name, (operand[1],)
        )

    def _compare(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.Compare)  # noqa: S101 - dispatch guarantees the type
        operands = [self.expr(node.left), *(self.expr(item) for item in node.comparators)]
        result = True
        for op, left, right in zip(node.ops, operands, operands[1:], strict=False):
            check = _COMPARE[type(op)]
            result = result and bool(
                self._apply(node, check, _plain(left[0].value), _plain(right[0].value))
            )
        label = join_all(*(_deep_label(ref) for ref in operands))
        inputs = tuple(ref[1] for ref in operands)
        return self._new(result, label, EventKind.OPERATION, node, "Compare", inputs)

    def _bool(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.BoolOp)  # noqa: S101 - dispatch guarantees the type
        stop_on_true = isinstance(node.op, ast.Or)
        evaluated: list[_Ref] = []
        for operand in node.values:
            ref = self.expr(operand)
            evaluated.append(ref)
            if bool(_plain(ref[0].value)) is stop_on_true:
                break
        label = join_all(*(_deep_label(ref) for ref in evaluated))
        inputs = tuple(ref[1] for ref in evaluated)
        chosen = evaluated[-1][0].value
        return self._new(chosen, label, EventKind.OPERATION, node, type(node.op).__name__, inputs)

    def _call(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.Call)  # noqa: S101 - dispatch guarantees the type
        assert isinstance(node.func, ast.Name)  # noqa: S101 - parse_plan guarantees it
        name = node.func.id
        args = [self.expr(arg) for arg in node.args]
        kwargs = {kw.arg: self.expr(kw.value) for kw in node.keywords if kw.arg is not None}
        if name in BUILTINS:
            return self._builtin(node, name, args, kwargs)
        tool = self._tools.get(name)
        if tool is None:
            raise PlanError(PlanErrorCode.UNKNOWN_TOOL, _line(node), name)
        result: object = None
        # Any tool failure becomes one typed code, raised below outside the
        # handler: the tool's exception text may carry an injection and must
        # not ride along as ``__cause__`` or ``__context__`` (decision Q3).
        with contextlib.suppress(Exception):
            result = tool(*(ref[0] for ref in args), **{key: ref[0] for key, ref in kwargs.items()})
        if not isinstance(result, Labeled):
            raise PlanError(PlanErrorCode.TOOL_FAILED, _line(node), name)
        all_args = [*args, *kwargs.values()]
        label = join_all(result.label, *(_deep_label(ref) for ref in all_args))
        inputs = tuple(ref[1] for ref in all_args)
        return self._new(result.value, label, EventKind.CALL, node, name, inputs)

    def _builtin(
        self, node: ast.Call, name: str, args: list[_Ref], kwargs: Mapping[str, _Ref]
    ) -> _Ref:
        if kwargs or not args:
            raise PlanError(PlanErrorCode.BAD_ARGUMENTS, _line(node), name)
        inputs = tuple(ref[1] for ref in args)
        if name == "len":
            if len(args) != 1:
                raise PlanError(PlanErrorCode.BAD_ARGUMENTS, _line(node), name)
            # The length depends on the container, not on what is inside it.
            value = self._apply(node, len, args[0][0].value)
            return self._new(value, args[0][0].label, EventKind.OPERATION, node, name, inputs)
        if name == "abs" and len(args) != 1:
            raise PlanError(PlanErrorCode.BAD_ARGUMENTS, _line(node), name)
        functions: dict[str, Callable[..., object]] = {
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
        }
        function = functions[name]
        value = self._apply(node, function, *(_plain(ref[0].value) for ref in args))
        label = join_all(*(_deep_label(ref) for ref in args))
        return self._new(value, label, EventKind.OPERATION, node, name, inputs)

    def _attribute(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.Attribute)  # noqa: S101 - dispatch guarantees the type
        container = self.expr(node.value)
        return self._field(node, container, node.attr, ())

    def _field(self, node: ast.expr, container: _Ref, name: str, extra: tuple[_Ref, ...]) -> _Ref:
        record = container[0].value
        if not isinstance(record, Record):
            raise PlanError(PlanErrorCode.TYPE_MISMATCH, _line(node), name)
        field = record.get(name)
        if field is None:
            raise PlanError(PlanErrorCode.MISSING_FIELD, _line(node), name)
        label = join_all(field.label, container[0].label, *(ref[0].label for ref in extra))
        inputs = (container[1], *(ref[1] for ref in extra))
        return self._new(field.value, label, EventKind.FIELD, node, name, inputs)

    def _subscript(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.Subscript)  # noqa: S101 - dispatch guarantees the type
        container, index = self.expr(node.value), self.expr(node.slice)
        sequence, position = container[0].value, index[0].value
        if isinstance(sequence, Record) and type(position) is str:
            return self._field(node, container, position, (index,))
        if type(sequence) not in {tuple, str} or type(position) is not int:
            raise PlanError(PlanErrorCode.TYPE_MISMATCH, _line(node))
        try:
            element = sequence[position]  # type: ignore[index]
        except IndexError:
            raise PlanError(PlanErrorCode.INDEX_OUT_OF_RANGE, _line(node)) from None
        own = element.label if isinstance(element, Labeled) else container[0].label
        value = element.value if isinstance(element, Labeled) else element
        label = join_all(own, container[0].label, index[0].label)
        return self._new(value, label, EventKind.INDEX, node, "", (container[1], index[1]))

    def _sequence(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.Tuple | ast.List)  # noqa: S101 - dispatch guarantees the type
        items = [self.expr(item) for item in node.elts]
        value = tuple(ref[0] for ref in items)
        inputs = tuple(ref[1] for ref in items)
        # The structure is the plan's; each element keeps its own label.
        return self._new(value, self._literal_label, EventKind.BUILD, node, "sequence", inputs)

    def _record(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.Dict)  # noqa: S101 - dispatch guarantees the type
        names = [str(key.value) for key in node.keys if isinstance(key, ast.Constant)]
        items = [self.expr(value) for value in node.values]
        try:
            record = Record.of(dict(zip(names, (ref[0] for ref in items), strict=True)))
        except (ValueError, TypeError):
            raise PlanError(PlanErrorCode.TYPE_MISMATCH, _line(node), "record") from None
        inputs = tuple(ref[1] for ref in items)
        return self._new(record, self._literal_label, EventKind.BUILD, node, "record", inputs)

    def _format(self, node: ast.expr) -> _Ref:
        assert isinstance(node, ast.JoinedStr)  # noqa: S101 - dispatch guarantees the type
        parts: list[str] = []
        refs: list[_Ref] = []
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                ref = self.expr(part.value)
                refs.append(ref)
                parts.append(str(_plain(ref[0].value)))
                self._check_size("".join(parts), part)
            elif isinstance(part, ast.Constant):
                parts.append(str(part.value))
        label = join_all(self._literal_label, *(_deep_label(ref) for ref in refs))
        inputs = tuple(ref[1] for ref in refs)
        return self._new("".join(parts), label, EventKind.OPERATION, node, "format", inputs)

    # --- helpers ------------------------------------------------------------

    def _apply(self, node: ast.AST, function: Callable[..., object], *args: object) -> object:
        try:
            return function(*args)
        except ZeroDivisionError:
            raise PlanError(PlanErrorCode.DIVISION_BY_ZERO, _line(node)) from None
        except OverflowError:
            raise PlanError(PlanErrorCode.VALUE_TOO_LARGE, _line(node)) from None
        except (TypeError, ValueError):
            raise PlanError(PlanErrorCode.TYPE_MISMATCH, _line(node)) from None

    def _check_size(self, value: object, node: ast.AST) -> None:
        limits = self._limits
        too_large = (
            (type(value) is str and len(value) > limits.max_string)
            or (type(value) is tuple and len(value) > limits.max_items)
            or (type(value) is int and value.bit_length() > limits.max_int_bits)
            or (isinstance(value, Record) and len(value.fields) > limits.max_items)
        )
        if too_large:
            raise PlanError(PlanErrorCode.VALUE_TOO_LARGE, _line(node))

    def _new(
        self,
        value: object,
        label: Label,
        kind: EventKind,
        node: ast.AST,
        detail: str,
        inputs: tuple[int, ...],
    ) -> _Ref:
        self._check_size(value, node)
        try:
            labeled = Labeled(value, label)
        except (NonFiniteNumberError, ValueTooDeepError):
            raise PlanError(PlanErrorCode.VALUE_TOO_LARGE, _line(node)) from None
        except MutableValueError:
            raise PlanError(PlanErrorCode.TYPE_MISMATCH, _line(node)) from None
        value_id = len(self._trace)
        self._event(value_id, kind, node, detail, inputs, label)
        return labeled, value_id

    def _event(
        self,
        value_id: int,
        kind: EventKind,
        node: ast.AST,
        detail: str,
        inputs: tuple[int, ...],
        label: Label,
    ) -> None:
        self._trace.append(TraceEvent(value_id, kind, _line(node), detail, inputs, label))


_HANDLERS: Final[Mapping[type[ast.expr], Callable[[_Run, ast.expr], _Ref]]] = {
    ast.Constant: _Run._constant,  # noqa: SLF001 - dispatch table inside the module
    ast.Name: _Run._name,  # noqa: SLF001
    ast.BinOp: _Run._binary,  # noqa: SLF001
    ast.UnaryOp: _Run._unary,  # noqa: SLF001
    ast.Compare: _Run._compare,  # noqa: SLF001
    ast.BoolOp: _Run._bool,  # noqa: SLF001
    ast.Call: _Run._call,  # noqa: SLF001
    ast.Attribute: _Run._attribute,  # noqa: SLF001
    ast.Subscript: _Run._subscript,  # noqa: SLF001
    ast.Tuple: _Run._sequence,  # noqa: SLF001
    ast.List: _Run._sequence,  # noqa: SLF001
    ast.Dict: _Run._record,  # noqa: SLF001
    ast.JoinedStr: _Run._format,  # noqa: SLF001
}
