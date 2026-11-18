# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Defensive paths that a checked plan cannot reach, tested directly.

``parse_plan`` and the size limits keep plans away from these branches, so
they are exercised here through the module APIs. They must stay fail-closed
in case a later change to the plan language opens a way in.
"""

import ast

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.interpreter.evaluator import Evaluator
from taintwall.core.interpreter.limits import Guard, Limits
from taintwall.core.interpreter.operators import apply
from taintwall.core.interpreter.tools import Toolbox
from taintwall.core.interpreter.trace import EventKind, Origin
from taintwall.core.labels import Label
from taintwall.core.mint import Mint

NODE = ast.parse("x = 1").body[0]
LABEL = Label.of(Mint().plan())


def evaluator() -> Evaluator:
    return Evaluator(Toolbox({}), LABEL, Guard(Limits()))


def raises(error: type[BaseException]) -> object:
    raise error


def test_overflow_becomes_value_too_large() -> None:
    with pytest.raises(PlanError) as err:
        apply(NODE, raises, OverflowError)
    assert err.value.code is PlanErrorCode.VALUE_TOO_LARGE


def test_a_mutable_result_is_a_type_mismatch() -> None:
    with pytest.raises(PlanError) as err:
        evaluator().new(({"a": 1}, LABEL), Origin(EventKind.OPERATION, NODE))
    assert err.value.code is PlanErrorCode.TYPE_MISMATCH


@pytest.mark.parametrize("source", ["pass", "import os", "del x"])
def test_unknown_statements_fail_closed(source: str) -> None:
    with pytest.raises(PlanError) as err:
        evaluator().statement(ast.parse(source).body[0])
    assert err.value.code is PlanErrorCode.UNSUPPORTED
