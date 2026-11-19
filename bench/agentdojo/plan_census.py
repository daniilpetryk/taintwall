# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Count which Python constructs collected plans use, and why taintwall rejects them.

Reads the JSON lines written by ``plans.py`` (one file per model) and the
run's ``functions.json``, and prints a Markdown report: per model, how many
replies were cut at the token limit, the share of plans that are valid Python
and that ``parse_plan`` accepts (with 95% Wilson intervals), the first reason
it rejects the rest, the share of valid plans using each construct, and the
methods and builtins they call. Nothing is executed.

Usage (from the repo root)::

    make bench-census RUN=natural
"""

import ast
import dataclasses
import functools
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Final

from bench_common.stats import wilson

from taintwall.core.errors import PlanError
from taintwall.core.plan import parse_plan
from taintwall.core.syntax import LANGUAGE_VERSION

_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _has(*types: type[ast.AST]) -> Callable[[ast.AST], bool]:
    return lambda tree: any(isinstance(node, types) for node in ast.walk(tree))


def _method_calls(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        for node in ast.walk(tree)
    )


def _format_specs(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.FormattedValue) and (node.format_spec or node.conversion != -1)
        for node in ast.walk(tree)
    )


def _unpacking(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Assign | ast.For)
        and isinstance(node.targets[0] if isinstance(node, ast.Assign) else node.target, ast.Tuple)
        for node in ast.walk(tree)
    )


def _store_into(tree: ast.AST) -> bool:
    """``x[i] = v`` or ``x.f = v``: mutation, which immutable values forbid."""
    return any(
        isinstance(node, ast.Assign | ast.AugAssign)
        and any(
            isinstance(target, ast.Subscript | ast.Attribute)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
        for node in ast.walk(tree)
    )


FEATURES: Final[dict[str, Callable[[ast.AST], bool]]] = {
    "import": _has(ast.Import, ast.ImportFrom),
    "def": _has(ast.FunctionDef, ast.AsyncFunctionDef),
    "class": _has(ast.ClassDef),
    "lambda": _has(ast.Lambda),
    "try/except": _has(ast.Try, ast.TryStar),
    "with": _has(ast.With),
    "raise/assert": _has(ast.Raise, ast.Assert),
    "comprehension": _has(*_COMPREHENSIONS),
    "x += 1": _has(ast.AugAssign),
    "a, b = ...": _unpacking,
    "x[i] = v / x.f = v": _store_into,
    "method call": _method_calls,
    "slice": _has(ast.Slice),
    "f-string spec": _format_specs,
    "a if c else b": _has(ast.IfExp),
    "is / is not": lambda tree: any(
        isinstance(op, ast.Is | ast.IsNot)
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for op in node.ops
    ),
    "if": _has(ast.If),
    "for": _has(ast.For),
    "while": _has(ast.While),
    "break/continue": _has(ast.Break, ast.Continue),
    "dict literal": _has(ast.Dict),
    "**": lambda tree: any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) for node in ast.walk(tree)
    ),
}
"""Constructs to count: each maps to a check over a plan's syntax tree."""


def _called_names(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            yield node.func.id


def _method_names(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield node.func.attr


def _rejection(code: str) -> str | None:
    try:
        parse_plan(code)
    except PlanError as err:
        return f"{err.code.value}: {err.detail}" if err.detail else err.code.value
    return None


@dataclasses.dataclass
class Census:
    """Counts over one model's plans."""

    plans: int = 0
    cut: int = 0
    valid: int = 0
    accepted: int = 0
    features: Counter[str] = dataclasses.field(default_factory=Counter)
    rejections: Counter[str] = dataclasses.field(default_factory=Counter)
    builtins: Counter[str] = dataclasses.field(default_factory=Counter)
    methods: Counter[str] = dataclasses.field(default_factory=Counter)


def census(plans: list[dict[str, object]], offered: frozenset[str]) -> Census:
    """Counts over one model's plans; ``offered`` are the functions the prompt named."""
    result = Census(plans=len(plans))
    for plan in plans:
        result.cut += plan.get("finish_reason") == "length"
        code = plan["code"]
        if not isinstance(code, str):
            result.rejections["no code block"] += 1
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            result.rejections["not Python"] += 1
            continue
        result.valid += 1
        reason = _rejection(code)
        if reason is None:
            result.accepted += 1
        else:
            result.rejections[reason] += 1
        result.features.update(name for name, check in FEATURES.items() if check(tree))
        defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        called = set(_called_names(tree)) - offered - defined
        result.builtins.update(called)
        result.methods.update(set(_method_names(tree)))
    return result


def _feature_share(feature: str, data: Census) -> str:
    return _percent(data.features[feature], data.valid)


def _percent(count: int, total: int) -> str:
    return f"{100 * count / total:.0f}%" if total else "-"


def _interval(count: int, total: int) -> str:
    low, high = wilson(count, total)
    return f"{_percent(count, total)} ({100 * low:.0f}-{100 * high:.0f})" if total else "-"


def report(by_model: dict[str, Census]) -> str:
    """The census as Markdown."""
    models = sorted(by_model)
    lines = ["| | " + " | ".join(models) + " |", "|---" * (len(models) + 1) + "|"]

    def row(name: str, value: Callable[[Census], str]) -> None:
        cells = " | ".join(value(by_model[model]) for model in models)
        lines.append(f"| {name} | {cells} |")

    row("plans", lambda data: str(data.plans))
    row("cut at the token limit", lambda data: str(data.cut))
    row("valid Python", lambda data: _interval(data.valid, data.plans))
    row("accepted by parse_plan (95% CI)", lambda data: _interval(data.accepted, data.plans))
    for feature in FEATURES:
        row(feature, functools.partial(_feature_share, feature))
    sections = (
        ("Rejections", "rejections"),
        ("Builtins called", "builtins"),
        ("Methods called", "methods"),
    )
    for title, field in sections:
        lines += ["", f"**{title}** (plans, all models)", ""]
        total: Counter[str] = Counter()
        for data in by_model.values():
            total.update(getattr(data, field))
        lines += [f"- `{name}`: {count}" for name, count in total.most_common(25)]
    return "\n".join(lines)


def counts(by_model: dict[str, Census]) -> dict[str, object]:
    """The rates' parts per model, for ``census-v<language>.json``.

    The language version says which plan language counted ``accepted``, so a
    census stays a record of its time when the language grows.
    """
    models = {
        model: {
            "plans": data.plans,
            "cut": data.cut,
            "valid": data.valid,
            "accepted": data.accepted,
        }
        for model, data in sorted(by_model.items())
    }
    return {"language": LANGUAGE_VERSION, "models": models}


def main() -> int:
    """Print the census of a run's plans and write its counts to ``census.json``.

    ``census-v<language>.json`` is what the measurements page reads;
    ``tests/test_journal.py`` recomputes it from the plans, so it cannot drift.
    """
    directory = Path(sys.argv[1])
    offered = frozenset(json.loads((directory / "functions.json").read_text(encoding="utf-8")))
    by_model = {}
    for path in sorted(directory.glob("*.jsonl")):
        plans = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        by_model[path.stem.replace("__", "/")] = census(plans, offered)
    out = directory / f"census-v{LANGUAGE_VERSION}.json"
    out.write_text(json.dumps(counts(by_model), indent=2) + "\n", encoding="utf-8")
    print(report(by_model))  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":
    sys.exit(main())
