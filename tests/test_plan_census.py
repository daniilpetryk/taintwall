# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The plan census script: construct detection and rejection reasons."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "plan_census.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plan_census", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["plan_census"] = module
    spec.loader.exec_module(module)
    return module


census_module = _load()

NATURAL = """\
import datetime
names = [t.subject.lower() for t in get_most_recent_transactions(10)]
total = 0
total += 1
answer(f"{total:.2f}")
"""
ALLOWED = 'x = get_balance()\nif x > 1:\n    answer(f"{x}")\n'


def test_counts_constructs_and_reasons() -> None:
    plans = [
        {"code": NATURAL},
        {"code": ALLOWED},
        {"code": None},
        {"code": "x = ("},
    ]
    result = census_module.census(plans)
    assert (result.plans, result.valid, result.accepted) == (4, 2, 1)
    features = result.features
    assert {"import", "comprehension", "x += 1", "method call", "f-string spec", "if"} <= set(
        features
    )
    assert features["if"] == 1
    assert result.rejections == {
        "unsupported: Import": 1,
        "no code block": 1,
        "not Python": 1,
    }
    assert result.methods == {"lower": 1}


def test_called_builtins_exclude_tools_and_own_functions() -> None:
    code = "def f(x):\n    return x\ny = round(f(get_balance()))\nanswer(str(y))\n"
    result = census_module.census([{"code": code}])
    assert result.builtins == {"round": 1, "str": 1}


def test_report_has_a_row_per_construct() -> None:
    report = census_module.report({"m/a": census_module.census([{"code": ALLOWED}])})
    assert "| accepted by parse_plan | 100% |" in report
    assert "| if | 100% |" in report
    assert "| plans | 1 |" in report
