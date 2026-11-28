# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""What a plan's values can be, without running it: the checker's type inference.

Types are inferred flow-insensitively: a variable has the join of everything
assigned to it anywhere in the plan, to a fixed point, and anything unclear
is ``object``. A constant written in the plan is ``Known``: its value, not
only its type. ``may_fit`` is ``False`` only when no value with what is known
could have the expected type, so a check built on it never refuses a value
that might fit.
"""

import ast
import dataclasses
from collections.abc import Iterator, Mapping
from typing import Final

from taintwall.tools.schema import ListOf, Maybe, OneOf, RecordOf, Scalar, ValueType, accepts
from taintwall.tools.spec import ToolSpec

MAX_ROUNDS: Final = 64
"""Most passes of type inference; past it every variable is ``object``."""


@dataclasses.dataclass(frozen=True, slots=True)
class Known:
    """A constant written in the plan: its value is known, not only its type.

    Compared by type as well as value: ``True``, ``1`` and ``1.0`` are equal in
    Python but fit different parameters.
    """

    value: object
    kind: type = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        """Record the value's exact type."""
        object.__setattr__(self, "kind", type(self.value))


type Static = ValueType | Known
"""What the checker knows about an expression's value."""


def infer(tree: ast.Module, specs: Mapping[str, ToolSpec]) -> Mapping[str, Static]:
    """Each variable's join over every assignment, to a fixed point."""
    targets = list(assignments(tree))
    types: dict[str, Static] = {}
    for _ in range(MAX_ROUNDS):
        joined: dict[str, Static] = {}
        for binding in targets:
            kind = typeof(binding.value, types, specs)
            kind = _element_of(kind) if binding.element else kind
            joined[binding.name] = (
                _join(joined[binding.name], kind) if binding.name in joined else kind
            )
        if joined == types:
            return types
        types = joined
    return dict.fromkeys(types, Scalar.ANY)


@dataclasses.dataclass(frozen=True, slots=True)
class Binding:
    """A name a plan binds, and to what."""

    name: str
    value: ast.expr | None
    """``None``: unknown, as for ``x += 1``."""
    element: bool = False
    """The name is a loop variable: it takes each element of ``value``."""


def assignments(tree: ast.Module) -> Iterator[Binding]:
    """Every name a plan binds; ``parse_plan`` allows only plain names as targets."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            assert isinstance(node.targets[0], ast.Name)  # noqa: S101 - parse_plan guarantees it
            yield Binding(node.targets[0].id, node.value)
        elif isinstance(node, ast.AugAssign):
            assert isinstance(node.target, ast.Name)  # noqa: S101 - parse_plan guarantees it
            yield Binding(node.target.id, None)
        elif isinstance(node, ast.For):
            assert isinstance(node.target, ast.Name)  # noqa: S101 - parse_plan guarantees it
            yield Binding(node.target.id, node.iter, element=True)


def typeof(  # noqa: PLR0911 - one case per kind of expression
    node: ast.expr | None, types: Mapping[str, Static], specs: Mapping[str, ToolSpec]
) -> Static:
    """What is known about ``node``'s value."""
    match node:
        case ast.Constant(value):  # parse_plan allows atoms only
            return Known(value)
        case ast.Name(name):
            return types.get(name, Scalar.ANY)
        case ast.Tuple() | ast.List() | ast.Dict():
            return _container_type(node, types, specs)
        case ast.Call() | ast.Attribute() | ast.Subscript():
            return _access_type(node, types, specs)
        case ast.Compare() | ast.UnaryOp(ast.Not()):
            return Scalar.BOOL
        case ast.JoinedStr():
            return Scalar.STR
        case ast.IfExp(_, body, orelse):
            return _join(typeof(body, types, specs), typeof(orelse, types, specs))
        case _:
            return Scalar.ANY


def _container_type(
    node: ast.expr, types: Mapping[str, Static], specs: Mapping[str, ToolSpec]
) -> Static:
    """A tuple or list literal (a plan tuple), or a dict literal (a record).

    ``parse_plan`` allows only string constants as a dict literal's keys.
    """
    if isinstance(node, ast.Dict):
        # One value per key in a dict literal: never ragged.
        pairs = zip(node.keys, node.values, strict=True)  # pragma: no mutate - see above
        fields = tuple(
            (key.value, _widen(typeof(value, types, specs)))  # type: ignore[union-attr]
            for key, value in pairs
        )
        return RecordOf("dict", fields)
    assert isinstance(node, ast.Tuple | ast.List)  # noqa: S101 - typeof dispatches no other
    items = [_widen(typeof(item, types, specs)) for item in node.elts]
    same = bool(items) and all(kind == items[0] for kind in items)
    return ListOf(items[0] if same else Scalar.ANY)


def _access_type(
    node: ast.expr, types: Mapping[str, Static], specs: Mapping[str, ToolSpec]
) -> Static:
    """A call, a field read or an element read (``parse_plan`` allows no other callee)."""
    if isinstance(node, ast.Attribute):
        return _field_type(typeof(node.value, types, specs), node.attr)
    if isinstance(node, ast.Subscript):
        return _element_type(typeof(node.value, types, specs), node.slice)
    assert isinstance(node, ast.Call)  # noqa: S101 - typeof dispatches no other
    if isinstance(node.func, ast.Name):
        return _call_type(node.func.id, node, types, specs)
    assert isinstance(node.func, ast.Attribute)  # noqa: S101 - parse_plan allows no other
    return _method_type(node.func.attr, typeof(node.func.value, types, specs), node.args)


def _call_type(
    name: str, node: ast.Call, types: Mapping[str, Static], specs: Mapping[str, ToolSpec]
) -> Static:
    if name in specs:
        return specs[name].returns
    if name == "sorted" and node.args:
        sequence = _widen(typeof(node.args[0], types, specs))
        return sequence if isinstance(sequence, ListOf) else Scalar.ANY
    return _BUILTIN_RESULTS.get(name, Scalar.ANY)


_BUILTIN_RESULTS: Final[Mapping[str, ValueType]] = {
    "len": Scalar.INT,
    "bool": Scalar.BOOL,
    "str": Scalar.STR,
    "int": Scalar.INT,
    "float": Scalar.FLOAT,
    "range": ListOf(Scalar.INT),
    "any": Scalar.BOOL,
    "all": Scalar.BOOL,
}
"""What builtins return whatever their arguments; the others depend on them."""

_METHOD_RESULTS: Final[Mapping[str, ValueType]] = {
    "lower": Scalar.STR,
    "upper": Scalar.STR,
    "strip": Scalar.STR,
    "replace": Scalar.STR,
    "join": Scalar.STR,
    "startswith": Scalar.BOOL,
    "endswith": Scalar.BOOL,
    "split": ListOf(Scalar.STR),
}
"""What the string methods return."""


def _method_type(method: str, receiver: Static, args: list[ast.expr]) -> Static:
    if method == "get":
        if isinstance(receiver, RecordOf) and args and is_text(args[0]):
            field = _field_type(receiver, args[0].value)  # type: ignore[attr-defined]
            return Scalar.ANY if field is Scalar.ANY else Maybe(_widen(field))
        return Scalar.ANY
    return _METHOD_RESULTS[method]  # parse_plan allows only these and ``get``


def _field_type(record: Static, field: str) -> Static:
    if isinstance(record, RecordOf):
        return dict(record.fields).get(field, Scalar.ANY)
    return Scalar.ANY


def _element_type(container: Static, index: ast.expr) -> Static:
    if isinstance(container, RecordOf) and is_text(index):
        return _field_type(container, index.value)  # type: ignore[attr-defined]
    return _element_of(container)


def _element_of(container: Static) -> Static:
    """An element of a list, or a character of a string; else unknown."""
    if isinstance(container, ListOf):
        return container.item
    return Scalar.STR if _widen(container) is Scalar.STR else Scalar.ANY


def is_text(node: ast.expr | None) -> bool:
    """Whether ``node`` is a string constant written in the plan."""
    return isinstance(node, ast.Constant) and type(node.value) is str


def _widen(kind: Static) -> ValueType:
    """A known constant as its type; any other knowledge as it is."""
    if isinstance(kind, Known):
        return _SCALARS[type(kind.value)]
    return kind


_SCALARS: Final[Mapping[type, Scalar]] = {
    str: Scalar.STR,
    int: Scalar.INT,
    float: Scalar.FLOAT,
    bool: Scalar.BOOL,
    type(None): Scalar.NONE,
}


def _join(left: Static, right: Static) -> Static:
    """What is known of a value that is one of these two."""
    if left == right:
        return left
    wide_left, wide_right = _widen(left), _widen(right)
    return wide_left if wide_left == wide_right else Scalar.ANY


def may_fit(value: Static, expected: ValueType) -> bool:
    """Whether some value with what is known could have type ``expected``.

    ``False`` only when no such value could: a constant of the wrong type, a
    number where a list belongs, ``None`` where it is not allowed.
    """
    if value is Scalar.ANY or expected is Scalar.ANY:
        return True
    if isinstance(value, Known):
        return accepts(expected, value.value)
    if isinstance(value, Maybe):
        return may_fit(Scalar.NONE, expected) or may_fit(value.inner, expected)
    if isinstance(expected, Maybe):
        return value is Scalar.NONE or may_fit(value, expected.inner)
    return _same_kind(value, expected)


def _same_kind(value: ValueType, expected: ValueType) -> bool:
    """``may_fit`` once neither side is unknown, a constant or optional."""
    if isinstance(value, OneOf):
        return any(accepts(expected, item) for item in value.values)
    if isinstance(expected, OneOf):
        return isinstance(value, Scalar) and any(accepts(value, item) for item in expected.values)
    if isinstance(value, ListOf) and isinstance(expected, ListOf):
        return True  # it may be empty, and an empty tuple fits any list type
    # The same atom, an int for a float, or two records (their fields are
    # checked when the call runs); any other pair is two kinds of value.
    return (
        value is expected
        or (value, expected) == (Scalar.INT, Scalar.FLOAT)
        or (isinstance(value, RecordOf) and isinstance(expected, RecordOf))
    )
