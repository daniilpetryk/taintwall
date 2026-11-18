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


TESTS: Final = ROOT / "tests"


def modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def collect_test_files() -> list[Path]:
    return sorted(TESTS.rglob("*.py"))


def module_name(path: Path) -> str:
    """Dotted name of a source module: ``src/taintwall/core/x.py`` -> ``taintwall.core.x``."""
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def encapsulated_packages() -> set[str]:
    """Packages below the top level: ``taintwall.core.labels``, not ``taintwall.core``."""
    return {
        module_name(init)
        for init in SRC.rglob("__init__.py")
        if SRC not in {init.parent.parent, init.parent}
    }


def imported_modules(tree: ast.AST) -> set[str]:
    """Every ``taintwall`` module ``tree`` imports, by dotted name."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return {name for name in found if name.split(".")[0] == "taintwall"}


def reaches_inside(importer: str, imported: str, packages: set[str]) -> bool:
    """Whether ``importer`` uses a module inside a package it is not part of."""
    for package in packages:
        inside_package = imported.startswith(f"{package}.")
        importer_inside = importer == package or importer.startswith(f"{package}.")
        if inside_package and not importer_inside:
            return True
    return False


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
def test_packages_are_used_through_their_init(path: Path) -> None:
    importer = module_name(path)
    allowed = set(QUALITY["allowed_internal_imports"])
    leaks = [
        f"{importer} -> {imported}"
        for imported in sorted(imported_modules(parse(path)))
        if reaches_inside(importer, imported, encapsulated_packages())
    ]
    assert [leak for leak in leaks if leak not in allowed] == []


@pytest.mark.parametrize("path", collect_test_files(), ids=lambda path: path.name)
def test_test_modules_stay_small(path: Path) -> None:
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= QUALITY["max_test_module_lines"], f"{lines} lines: split the test module"


@pytest.mark.parametrize("path", collect_test_files(), ids=lambda path: path.name)
def test_test_functions_stay_short(path: Path) -> None:
    assert oversized_functions(parse(path), QUALITY["max_test_function_lines"]) == []


@pytest.mark.parametrize("path", modules(), ids=module_id)
def test_module_names_say_what_they_hold(path: Path) -> None:
    # "utils" or "helpers" collect unrelated code; name the responsibility.
    assert path.stem not in QUALITY["banned_module_names"]


def test_the_checks_see_violations() -> None:
    long_function = "def f():\n" + "    x = 1\n" * 50
    crowded = "class C:\n" + "".join(f"    def m{i}(self): pass\n" for i in range(20))
    assert oversized_functions(ast.parse(long_function), 40) == ["f (line 1): 51 lines"]
    assert crowded_classes(ast.parse(crowded), 15) == ["C (line 1): 20 methods"]
    packages = {"taintwall.core.labels"}
    assert reaches_inside("taintwall.policy", "taintwall.core.labels.source", packages)
    assert not reaches_inside("taintwall.policy", "taintwall.core.labels", packages)
    assert not reaches_inside(
        "taintwall.core.labels.label", "taintwall.core.labels.source", packages
    )
    assert imported_modules(
        ast.parse("import taintwall.a.b\nfrom taintwall.c import d\nimport os")
    ) == {
        "taintwall.a.b",
        "taintwall.c",
    }


def test_encapsulated_packages_are_found() -> None:
    assert {"taintwall.core.labels", "taintwall.core.interpreter"} <= encapsulated_packages()
    assert "taintwall.core" not in encapsulated_packages()


def test_the_policy_is_configured() -> None:
    assert set(QUALITY) == {
        "max_module_lines",
        "max_function_lines",
        "max_class_methods",
        "banned_module_names",
        "max_test_module_lines",
        "max_test_function_lines",
        "allowed_internal_imports",
        "min_mutation_score",
    }
    assert len(modules()) >= 20
