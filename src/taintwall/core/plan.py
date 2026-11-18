# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The plan language: a small, checked subset of Python.

The P-LLM writes a plan as Python source. ``parse_plan`` accepts it only if
every syntax node is on an allowlist, so the interpreter never sees imports,
function or class definitions, lambdas, attribute access to private names,
method calls or anything else outside the language. Size and depth are
bounded before evaluation, so a plan cannot exhaust memory or the stack.

Stage 1, PR 2 covers straight-line plans (explicit flows). ``if``, ``for`` and
the other control-flow constructs arrive with implicit-flow tracking.
"""

import ast
import dataclasses
import io
import tokenize
from collections.abc import Callable
from typing import Final

from taintwall.core.errors import PlanError, PlanErrorCode

MAX_PLAN_CHARS: Final = 20_000
"""Longest accepted plan source."""

MAX_PLAN_DEPTH: Final = 40
"""Deepest accepted syntax-tree nesting."""

_ALLOWED_NODES: Final = frozenset(
    {
        ast.Module,
        ast.Assign,
        ast.Expr,
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
        ast.BoolOp,
        ast.And,
        ast.Or,
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


@dataclasses.dataclass(frozen=True, slots=True)
class Plan:
    """A plan that passed every static check."""

    source: str
    tree: ast.Module


def parse_plan(source: str) -> Plan:
    """Parse and check a plan.

    Raises:
        PlanError: ``TOO_LARGE``, ``SYNTAX``, ``UNSUPPORTED`` or ``TOO_DEEP``.
    """
    if len(source) > MAX_PLAN_CHARS:
        raise PlanError(PlanErrorCode.TOO_LARGE)
    try:
        tree = ast.parse(source)
    except SyntaxError as err:
        raise PlanError(PlanErrorCode.SYNTAX, err.lineno) from None
    except ValueError:
        # Older CPython raised ValueError for some malformed sources.
        raise PlanError(PlanErrorCode.SYNTAX) from None
    except (MemoryError, RecursionError):
        # CPython's parser gives up on very deep nesting before our check.
        raise PlanError(PlanErrorCode.TOO_DEEP) from None
    _check_ascii_names(source)
    _check_depth(tree)
    for node in ast.walk(tree):
        _check_node(node)
    return Plan(source, tree)


def _check_depth(tree: ast.Module) -> None:
    # Nodes such as ``Load`` have no line; they report their parent's.
    pending: list[tuple[ast.AST, int, int | None]] = [(tree, 0, None)]
    while pending:
        node, depth, line = pending.pop()
        line = getattr(node, "lineno", line)
        if depth > MAX_PLAN_DEPTH:
            raise PlanError(PlanErrorCode.TOO_DEEP, line)
        pending.extend((child, depth + 1, line) for child in ast.iter_child_nodes(node))


def _unsupported(node: ast.AST, detail: str) -> PlanError:
    return PlanError(PlanErrorCode.UNSUPPORTED, getattr(node, "lineno", None), detail)


def _check_node(node: ast.AST) -> None:
    if type(node) not in _ALLOWED_NODES:
        raise _unsupported(node, type(node).__name__)
    check = _NODE_CHECKS.get(type(node))
    if check is not None:
        check(node)


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


def _check_ascii_names(source: str) -> None:
    """Reject identifiers that are not ASCII in the source text.

    Python folds identifiers with NFKC before the syntax tree exists, so a
    fullwidth or look-alike name would run as a different name than the one
    an approver reads. Strings may hold any text; names may not.
    """
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type == tokenize.NAME and not token.string.isascii():
            raise PlanError(PlanErrorCode.UNSUPPORTED, token.start[0], "non-ascii name")


def _check_constant(node: ast.AST) -> None:
    assert isinstance(node, ast.Constant)  # noqa: S101 - dispatch guarantees the type
    if type(node.value) not in _ALLOWED_CONSTANTS:
        raise _unsupported(node, "constant type")


def _check_formatted(node: ast.AST) -> None:
    assert isinstance(node, ast.FormattedValue)  # noqa: S101 - dispatch guarantees the type
    if node.conversion != -1 or node.format_spec is not None:
        raise _unsupported(node, "format spec")


def _check_call(node: ast.AST) -> None:
    assert isinstance(node, ast.Call)  # noqa: S101 - dispatch guarantees the type
    if not isinstance(node.func, ast.Name):
        raise _unsupported(node, "method call")
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
    ast.Name: _check_name,
    ast.Attribute: _check_attribute,
    ast.Constant: _check_constant,
    ast.Call: _check_call,
    ast.FormattedValue: _check_formatted,
    ast.Dict: _check_dict,
}
