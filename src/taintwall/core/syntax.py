# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Which syntax the plan language allows, node by node.

An allowlist of syntax-tree node types, and a check per node type for the
narrower rules: one-name assignment targets, no private names, constants of
plain types, calls by name or to known methods, string-keyed records,
fixed-point number formats, identity only against ``None``, loop shapes, and
``break``/``continue`` only inside a loop. ``plan.parse_plan`` applies them.
"""

import ast
import re
from collections.abc import Callable
from typing import Final

from taintwall.core.errors import PlanError, PlanErrorCode

LANGUAGE_VERSION: Final = 2
"""Which plans the language accepts. Bump it whenever that set changes: a stored
plan census says which version it counted, so an old census stays a record of
its time instead of being recounted (version 2 adds the constructs from
``docs/planner-plans.md``)."""

STRING_METHODS: Final = frozenset(
    {"lower", "upper", "strip", "startswith", "endswith", "replace", "split", "join"}
)
"""Methods a plan may call on a string: pure, and bounded in size before they run."""

METHODS: Final = STRING_METHODS | {"get"}
"""Every method a plan may call; ``get`` is ``record.get(field, default)``."""

NUMBER_FORMAT: Final = re.compile(r",?\.\df")
"""The only f-string format specs: fixed-point numbers such as ``.2f`` or ``,.2f``."""

_AUGMENTED: Final = (ast.Add, ast.Sub, ast.Mult)
"""Operators of ``x += y``, ``x -= y`` and ``x *= y``."""

_ALLOWED_NODES: Final = frozenset(
    {
        ast.Module,
        ast.Assign,
        ast.AugAssign,
        ast.Expr,
        ast.If,
        ast.For,
        ast.While,
        ast.Break,
        ast.Continue,
        ast.Pass,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Constant,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.UnaryOp,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.In,
        ast.NotIn,
        ast.Is,
        ast.IsNot,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.IfExp,
        ast.Call,
        ast.keyword,
        ast.Attribute,
        ast.Subscript,
        ast.Tuple,
        ast.List,
        ast.Dict,
        ast.JoinedStr,
        ast.FormattedValue,
    }
)
_ALLOWED_CONSTANTS: Final = frozenset({str, int, float, bool, type(None)})


def _unsupported(node: ast.AST, detail: str) -> PlanError:
    return PlanError(PlanErrorCode.UNSUPPORTED, getattr(node, "lineno", None), detail)


def check_node(node: ast.AST) -> None:
    """Reject a node outside the language, or one that breaks its node type's rule."""
    if type(node) not in _ALLOWED_NODES:
        raise _unsupported(node, type(node).__name__)
    check = _NODE_CHECKS.get(type(node))
    if check is not None:
        check(node)


def check_loop_exits(tree: ast.Module) -> None:
    """Reject ``break`` and ``continue`` outside a loop.

    Python reports this only when compiling, after ``ast.parse`` succeeded.
    """
    pending: list[tuple[ast.AST, bool]] = [(tree, False)]
    while pending:
        node, in_loop = pending.pop()
        if isinstance(node, ast.Break | ast.Continue) and not in_loop:
            raise _unsupported(node, "exit outside loop")
        inner = in_loop or isinstance(node, ast.For | ast.While)
        pending.extend((child, inner) for child in ast.iter_child_nodes(node))


def _check_loop(node: ast.AST) -> None:
    assert isinstance(node, ast.For | ast.While)  # noqa: S101 - dispatch guarantees the type
    if node.orelse:
        raise _unsupported(node, "loop else")
    if isinstance(node, ast.For) and not isinstance(node.target, ast.Name):
        raise _unsupported(node, "loop target")


def _check_assign(node: ast.AST) -> None:
    assert isinstance(node, ast.Assign)  # noqa: S101 - dispatch guarantees the type
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        raise _unsupported(node, "assignment target")


def _check_name(node: ast.AST) -> None:
    assert isinstance(node, ast.Name)  # noqa: S101 - dispatch guarantees the type
    _check_identifier(node, node.id)


def _check_attribute(node: ast.AST) -> None:
    assert isinstance(node, ast.Attribute)  # noqa: S101 - dispatch guarantees the type
    _check_identifier(node, node.attr)


def _check_identifier(node: ast.AST, name: str) -> None:
    if name.startswith("_"):
        raise _unsupported(node, "private name")


def _check_constant(node: ast.AST) -> None:
    assert isinstance(node, ast.Constant)  # noqa: S101 - dispatch guarantees the type
    if type(node.value) not in _ALLOWED_CONSTANTS:
        raise _unsupported(node, "constant type")


def _check_formatted(node: ast.AST) -> None:
    assert isinstance(node, ast.FormattedValue)  # noqa: S101 - dispatch guarantees the type
    if node.conversion != -1 or not _number_format(node.format_spec):
        raise _unsupported(node, "format spec")


def _number_format(spec: ast.expr | None) -> bool:
    """No spec, or one constant fixed-point spec such as ``.2f``."""
    if spec is None:
        return True
    assert isinstance(spec, ast.JoinedStr)  # noqa: S101 - Python parses a spec as an f-string
    parts = spec.values
    return (
        len(parts) == 1
        and isinstance(parts[0], ast.Constant)
        and NUMBER_FORMAT.fullmatch(str(parts[0].value)) is not None
    )


def _check_compare(node: ast.AST) -> None:
    """``is`` and ``is not`` only test for ``None``: identity of anything else is not data."""
    assert isinstance(node, ast.Compare)  # noqa: S101 - dispatch guarantees the type
    # Python's parser gives one comparator per operator; strict only documents it.
    for op, right in zip(node.ops, node.comparators, strict=True):  # pragma: no mutate
        is_none = isinstance(right, ast.Constant) and right.value is None
        if isinstance(op, ast.Is | ast.IsNot) and not is_none:
            raise _unsupported(node, "is")


def _check_augmented(node: ast.AST) -> None:
    assert isinstance(node, ast.AugAssign)  # noqa: S101 - dispatch guarantees the type
    if not isinstance(node.target, ast.Name) or not isinstance(node.op, _AUGMENTED):
        raise _unsupported(node, "augmented assignment")


def _check_callee(node: ast.Call) -> None:
    """A tool or builtin by plain name, or a known method called positionally."""
    if isinstance(node.func, ast.Attribute):
        if node.func.attr not in METHODS:
            raise _unsupported(node, "method call")
        if node.keywords:
            raise _unsupported(node, "method keyword")
    elif not isinstance(node.func, ast.Name):
        raise _unsupported(node, "call target")


def _check_call(node: ast.AST) -> None:
    assert isinstance(node, ast.Call)  # noqa: S101 - dispatch guarantees the type
    _check_callee(node)
    if any(isinstance(arg, ast.Starred) for arg in node.args):
        raise _unsupported(node, "star argument")
    if any(keyword.arg is None for keyword in node.keywords):
        raise _unsupported(node, "keyword unpacking")
    for keyword in node.keywords:
        if keyword.arg is not None:
            _check_identifier(node, keyword.arg)


def _check_dict(node: ast.AST) -> None:
    assert isinstance(node, ast.Dict)  # noqa: S101 - dispatch guarantees the type
    for key in node.keys:
        if not (isinstance(key, ast.Constant) and type(key.value) is str):
            raise _unsupported(node, "record key")


_NODE_CHECKS: Final[dict[type[ast.AST], Callable[[ast.AST], None]]] = {
    ast.Assign: _check_assign,
    ast.For: _check_loop,
    ast.While: _check_loop,
    ast.Name: _check_name,
    ast.Attribute: _check_attribute,
    ast.Constant: _check_constant,
    ast.Call: _check_call,
    ast.Compare: _check_compare,
    ast.AugAssign: _check_augmented,
    ast.FormattedValue: _check_formatted,
    ast.Dict: _check_dict,
}
