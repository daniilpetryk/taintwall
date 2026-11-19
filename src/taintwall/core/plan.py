# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The plan language: a small, checked subset of Python.

The P-LLM writes a plan as Python source. ``parse_plan`` accepts it only if
every syntax node is on an allowlist, so the interpreter never sees imports,
function or class definitions, lambdas, attribute access to private names,
method calls or anything else outside the language. Size and depth are
bounded before evaluation, so a plan cannot exhaust memory or the stack.

Control flow is ``if``/``elif``/``else``, ``for name in tuple``, ``while``,
``break``, ``continue`` and ``pass``; loops have no ``else`` clause, and
``break`` and ``continue`` must be inside a loop.

What planners keep writing even when told the rules (``docs/planner-plans.md``)
is allowed in a narrow form: the string methods in ``STRING_METHODS`` and
``record.get``, fixed-point number formats in f-strings, ``x is None`` and
``x is not None``, ``a if c else b``, and ``x += y`` (also ``-=``, ``*=``)
on a plain name.
"""

import ast
import dataclasses
import io
import tokenize
from typing import Final

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.syntax import check_loop_exits, check_node

MAX_PLAN_CHARS: Final = 20_000
"""Longest accepted plan source."""

MAX_PLAN_DEPTH: Final = 40
"""Deepest accepted syntax-tree nesting."""


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
        check_node(node)
    check_loop_exits(tree)
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
