# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Clean-code limits that ruff cannot express: module, function and class size.

The numbers live in ``[tool.taintwall.quality]`` in ``pyproject.toml`` next to
the ruff limits, so the whole policy is in one file; see
``docs/code-quality.md``. A failure names the offending file and line.
"""

import ast
import tomllib
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parent.parent
SRC: Final = ROOT / "src" / "taintwall"
QUALITY: Final = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
    "taintwall"
]["quality"]


def modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def module_id(path: Path) -> str:
    return str(path.relative_to(SRC))


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def oversized_functions(tree: ast.AST, limit: int) -> list[str]:
    """``name (line N): M lines`` for every function longer than ``limit``."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > limit:
                found.append(f"{node.name} (line {node.lineno}): {length} lines")
    return found


def crowded_classes(tree: ast.AST, limit: int) -> list[str]:
    """``name (line N): M methods`` for every class with more than ``limit`` methods."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [
                item
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            if len(methods) > limit:
                found.append(f"{node.name} (line {node.lineno}): {len(methods)} methods")
    return found


@pytest.mark.parametrize("path", modules(), ids=module_id)
def test_modules_stay_small(path: Path) -> None:
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= QUALITY["max_module_lines"], f"{lines} lines: split the module"


@pytest.mark.parametrize("path", modules(), ids=module_id)
def test_functions_stay_short(path: Path) -> None:
    assert oversized_functions(parse(path), QUALITY["max_function_lines"]) == []


@pytest.mark.parametrize("path", modules(), ids=module_id)
def test_classes_stay_focused(path: Path) -> None:
    assert crowded_classes(parse(path), QUALITY["max_class_methods"]) == []


@pytest.mark.parametrize("path", modules(), ids=module_id)
def test_module_names_say_what_they_hold(path: Path) -> None:
    # "utils" or "helpers" collect unrelated code; name the responsibility.
    assert path.stem not in QUALITY["banned_module_names"]


def test_the_checks_see_violations() -> None:
    long_function = "def f():\n" + "    x = 1\n" * 50
    crowded = "class C:\n" + "".join(f"    def m{i}(self): pass\n" for i in range(20))
    assert oversized_functions(ast.parse(long_function), 40) == ["f (line 1): 51 lines"]
    assert crowded_classes(ast.parse(crowded), 15) == ["C (line 1): 20 methods"]


def test_the_policy_is_configured() -> None:
    assert set(QUALITY) == {
        "max_module_lines",
        "max_function_lines",
        "max_class_methods",
        "banned_module_names",
    }
    assert len(modules()) >= 20
