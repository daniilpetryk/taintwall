# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""An AgentDojo suite's functions as a taintwall tool registry.

Each function becomes a ``ToolSpec``: parameters from its pydantic input
schema, its result type from its return annotation, its description from
AgentDojo's own source (trusted configuration, not a tool server). Effects
and readers are this file's choices, per suite: unknown functions count as
changing something, the fail-safe default for the replanning rule (Q3).
The implementation runs the function in the task's environment through
AgentDojo's runtime; an error comes back as an exception, so the interpreter
reports ``TOOL_FAILED`` and drops its text (it may carry an injection).
"""

from collections.abc import Mapping
from typing import Any, Final

from agentdojo.functions_runtime import FunctionsRuntime
from pydantic import TypeAdapter

from taintwall.core.labels import Readers
from taintwall.tools import (
    Effects,
    Implementation,
    Param,
    Registry,
    ToolSpec,
    from_json_schema,
)

# A transfer is outward too: its subject reaches a recipient the plan names.
_FUNDS: Final = Effects(moves_funds=True, outward=True)
_WRITES: Final = Effects(writes=True)
_READS: Final = Effects()

EFFECTS: Final[Mapping[str, Mapping[str, Effects]]] = {
    "banking": {
        "get_iban": _READS,
        "get_balance": _READS,
        "get_most_recent_transactions": _READS,
        "get_scheduled_transactions": _READS,
        "read_file": _READS,
        "get_user_info": _READS,
        "send_money": _FUNDS,
        "schedule_transaction": _FUNDS,
        "update_scheduled_transaction": _FUNDS,
        "update_password": _WRITES,
        "update_user_info": _WRITES,
    },
}
"""What each function may change, per suite; anything unlisted is ``writes``."""

READERS: Final[Mapping[str, Readers]] = {"banking": Readers.USER}
"""Who may see a suite's results (Q9): bank data is the user's own."""


class ToolFailedError(RuntimeError):
    """An AgentDojo function reported an error; its text stays out of the plan."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} failed")


def spec_for(function: Any, suite: str) -> ToolSpec:  # noqa: ANN401 - AgentDojo's Function is untyped
    """The spec of one AgentDojo function."""
    schema = function.parameters.model_json_schema()
    defs = schema.get("$defs", {})
    fields = function.parameters.model_fields
    params = tuple(
        Param(name, from_json_schema(prop, defs))
        if fields[name].is_required()
        # The real default, factories included: the JSON schema omits those.
        else Param(
            name, from_json_schema(prop, defs), fields[name].get_default(call_default_factory=True)
        )
        for name, prop in schema.get("properties", {}).items()
    )
    returns = from_json_schema(TypeAdapter(function.return_type).json_schema())
    return ToolSpec(
        name=function.name,
        description=function.description,
        params=params,
        returns=returns,
        effects=EFFECTS.get(suite, {}).get(function.name, _WRITES),
        readers=READERS.get(suite, Readers.USER),
    )


def registry(suite: Any, environment: Any, suite_name: str) -> Registry:  # noqa: ANN401 - untyped
    """Every function of ``suite`` as a tool acting on ``environment``."""
    runtime = FunctionsRuntime(suite.tools)
    return Registry(
        (spec_for(function, suite_name), _implementation(runtime, environment, function.name))
        for function in suite.tools
    )


def _implementation(runtime: FunctionsRuntime, environment: Any, name: str) -> Implementation:  # noqa: ANN401
    def run(**arguments: object) -> object:
        result, error = runtime.run_function(environment, name, arguments)
        if error is not None:
            raise ToolFailedError(name)
        return result

    return run
