# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The interpreter entry point: one requester, one set of tools, many runs."""

from collections.abc import Mapping
from typing import Final

from taintwall.core.interpreter.control import Flow, ImplicitFlows
from taintwall.core.interpreter.evaluator import Evaluator, RunResult
from taintwall.core.interpreter.limits import Guard, Limits
from taintwall.core.interpreter.statements import block
from taintwall.core.interpreter.tools import Tool, Toolbox
from taintwall.core.labels import Label, Source, SourceKind
from taintwall.core.mint import Mint
from taintwall.core.plan import Plan

_REQUESTER_KINDS: Final = frozenset({SourceKind.OWNER, SourceKind.PRINCIPAL})


class InvalidRequesterError(ValueError):
    """The requester is not the owner or a precise principal."""

    def __init__(self) -> None:
        super().__init__("requester must be the owner or a precise principal source")


class InvalidFlowsError(ValueError):
    """The implicit-flow mode is not an ``ImplicitFlows`` member."""

    def __init__(self) -> None:
        super().__init__("flows must be an ImplicitFlows member")


class Interpreter:
    """Runs plans against a fixed set of tools on behalf of one requester.

    Literals carry the plan and the requester, so a plan written from a
    stranger's request never produces owner-trusted constants (contribution C).
    ``flows`` chooses whether control dependencies enter values (Q2).
    """

    def __init__(
        self,
        tools: Mapping[str, Tool],
        *,
        requester: Source,
        mint: Mint,
        limits: Limits | None = None,
        flows: ImplicitFlows = ImplicitFlows.STRICT,
    ) -> None:
        if requester.kind not in _REQUESTER_KINDS or requester.is_category:
            raise InvalidRequesterError
        self._tools = Toolbox(tools)
        self._literal_label = Label.of(mint.plan(), requester)
        self._limits = limits or Limits()
        if not isinstance(flows, ImplicitFlows):
            # Like source kinds: a plain string is never taken for a mode.
            raise InvalidFlowsError
        self._flows = flows

    def run(self, plan: Plan) -> RunResult:
        """Run ``plan`` once from an empty environment.

        Raises:
            LabeledPlanError: with a typed code and the label of what the
                failure depends on, if the plan fails.
        """
        evaluator = Evaluator(
            self._tools, self._literal_label, Guard(self._limits), Flow(self._flows)
        )
        block(evaluator, plan.tree.body)
        return evaluator.result()
