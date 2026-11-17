# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for the package skeleton."""

import importlib

import pytest

import taintwall

SUBPACKAGES = ["core", "llm", "policy", "store", "tools", "web3"]


def test_version_matches_metadata() -> None:
    assert taintwall.__version__ == "0.0.0"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    module = importlib.import_module(f"taintwall.{name}")
    assert module.__doc__
