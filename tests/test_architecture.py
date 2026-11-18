# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Architecture guards that import contracts cannot express.

The mint capability token lives in ``labels.py`` (which every layer imports),
so an import contract cannot stop ``from taintwall.core.labels import
_MINT_TOKEN``. This test parses every source file and allows the token, and the
``_mint`` argument that carries it, only where minting is implemented.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "taintwall"
MINT_HOMES = {SRC / "core" / "labels.py", SRC / "core" / "mint.py"}
FORBIDDEN_NAMES = {"_MINT_TOKEN", "_MintToken"}
FORBIDDEN_ARGUMENT = "_mint"


def mint_uses(tree: ast.AST) -> list[str]:
    """Every place in ``tree`` that touches the mint capability."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"line {node.lineno}: name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES | {
            FORBIDDEN_ARGUMENT
        }:
            found.append(f"line {node.lineno}: attribute {node.attr}")
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in FORBIDDEN_NAMES:
            found.append(f"line {node.lineno}: import {node.name}")
        elif isinstance(node, ast.keyword) and node.arg == FORBIDDEN_ARGUMENT:
            found.append(f"line {node.lineno}: argument {node.arg}=")
    return found


def source_files() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if path not in MINT_HOMES)


@pytest.mark.parametrize("path", source_files(), ids=lambda path: str(path.relative_to(SRC)))
def test_only_the_mint_touches_the_capability(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert mint_uses(tree) == []


@pytest.mark.parametrize(
    "code",
    [
        "from taintwall.core.labels import _MINT_TOKEN",
        "import taintwall.core.labels as labels\nlabels._MINT_TOKEN",
        "Source(kind, name, _mint=token)",
        "source._mint",
        "class Fake(_MintToken): pass",
    ],
)
def test_the_guard_sees_every_bypass(code: str) -> None:
    assert mint_uses(ast.parse(code)) != []


def test_the_guard_covers_real_files() -> None:
    assert len(source_files()) >= 7
    assert all(home.exists() for home in MINT_HOMES)
