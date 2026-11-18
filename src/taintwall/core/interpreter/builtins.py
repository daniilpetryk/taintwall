# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Builtin functions of the plan language: ``len``, ``abs``, ``min``, ``max``, ``sum``.

``len`` reads only the container label: a count does not depend on what the
elements are. The others read every nested element, so they carry deep
labels, and they must return an atom.
"""

import ast
from collections.abc import Callable, Mapping
from typing import Final

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.arguments import Arguments
from taintwall.core.interpreter.limits import Guard
from taintwall.core.interpreter.location import error_at
from taintwall.core.interpreter.operators import ATOMS, apply
from taintwall.core.interpreter.trace import Result
from taintwall.core.interpreter.walk import deep_label, plain
from taintwall.core.labels import Labeled, join_all

_FUNCTIONS: Final[Mapping[str, tuple[Callable[..., object], int | None]]] = {
    "abs": (abs, 1),
    "min": (min, None),
    "max": (max, None),
    "sum": (sum, 1),
}
"""Builtins other than ``len``, with their fixed arity (``None``: any)."""

BUILTINS: Final = frozenset({"len", *_FUNCTIONS})
"""Functions every plan can call besides tools."""


def call_builtin(node: ast.Call, name: str, arguments: Arguments, guard: Guard) -> Result:
    """Call builtin ``name``; builtins take positional arguments only."""
    args = arguments.positional
    if arguments.keyword or not args:
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node, name)
    if name == "len":
        return _length(node, args)
    function, arity = _FUNCTIONS[name]
    if arity is not None and len(args) != arity:
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node, name)
    value = apply(node, function, *(plain(item.value, guard) for item in args))
    if type(value) not in ATOMS:
        # min/max over sequences of containers: results must be atoms.
        raise error_at(PlanErrorCode.TYPE_MISMATCH, node, name)
    return value, join_all(*(deep_label(item, guard) for item in args))


def _length(node: ast.Call, args: tuple[Labeled[object], ...]) -> Result:
    if len(args) != 1:
        raise error_at(PlanErrorCode.BAD_ARGUMENTS, node, "len")
    # The length depends on the container, not on what is inside it.
    return apply(node, len, args[0].value), args[0].label
