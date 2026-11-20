# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""An AgentDojo suite's functions as a taintwall tool registry.

Each function becomes a ``ToolSpec``: parameters from its pydantic input
schema, its result type from its return annotation, its description from
AgentDojo's own source (trusted configuration, not a tool server). Effects
and readers are this file's choices, per suite: an unlisted function counts
as having every effect (writes, moves funds, sends outward), the fail-safe
default for the replanning rule (Q3) and for policies.
The implementation runs the function in the task's environment through
AgentDojo's runtime and dumps its result as JSON would (by alias), so values
match the serialization schema the result type was read from; an error comes
back as an exception, so the interpreter reports ``TOOL_FAILED`` and drops its
text (it may carry an injection). A function whose schema the tool types
cannot express is left out of the registry and reported by name, so one
function does not cost the whole suite.
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
    UnsupportedSchemaError,
    from_json_schema,
)

# A transfer is outward too: its subject reaches a recipient the plan names.
_FUNDS: Final = Effects(moves_funds=True, outward=True)
_WRITES: Final = Effects(writes=True)
_READS: Final = Effects()
_UNKNOWN: Final = Effects(writes=True, moves_funds=True, outward=True)

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
"""What each function may change, per suite; anything unlisted has every effect."""

READERS: Final[Mapping[str, Readers]] = {"banking": Readers.USER}
"""Who may see a suite's results (Q9): bank data is the user's own."""


class ToolFailedError(RuntimeError):
    """An AgentDojo function reported an error; its text stays out of the plan."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} failed")


class UnsupportedFunctionError(ValueError):
    """A function's parameter or result has a schema the tool types cannot express."""

    def __init__(self, function: str, part: str, reason: UnsupportedSchemaError) -> None:
        super().__init__(f"{function} {part}: {reason}")


def spec_for(function: Any, suite: str) -> ToolSpec:  # noqa: ANN401 - AgentDojo's Function is untyped
    """The spec of one AgentDojo function.

    Raises:
        UnsupportedFunctionError: naming the parameter or the result refused.
    """
    schema = function.parameters.model_json_schema()
    defs = schema.get("$defs", {})
    properties = schema.get("properties", {})
    # Properties come in field order, named as the model validates its input
    # (alias, validation alias, AliasChoices, AliasPath): pair them by position.
    fields = function.parameters.model_fields.values()
    params = tuple(
        _param(function.name, name, prop, defs, field)
        for field, (name, prop) in zip(fields, properties.items(), strict=True)
    )
    # Results are dumped as they serialize (implementation), so their type is
    # read from the serialization schema: its aliases, its computed fields.
    try:
        returns = from_json_schema(
            TypeAdapter(function.return_type).json_schema(mode="serialization")
        )
    except UnsupportedSchemaError as err:
        raise UnsupportedFunctionError(function.name, "result", err) from None
    return ToolSpec(
        name=function.name,
        description=function.description,
        params=params,
        returns=returns,
        effects=EFFECTS.get(suite, {}).get(function.name, _UNKNOWN),
        readers=READERS.get(suite, Readers.USER),
    )


def _param(function: str, name: str, prop: Any, defs: Any, field: Any) -> Param:  # noqa: ANN401
    """One parameter, its description from the schema, its real default if optional."""
    try:
        kind = from_json_schema(prop, defs)
    except UnsupportedSchemaError as err:
        raise UnsupportedFunctionError(function, f"parameter {name}", err) from None
    description = str(prop.get("description", ""))
    if field.is_required():
        return Param(name, kind, description=description)
    # The real default, factories included (the JSON schema omits those); one
    # computed from the other arguments has no value to show.
    if field.default_factory_takes_validated_data:
        return Param(name, kind, ..., description=description)
    return Param(name, kind, field.get_default(call_default_factory=True), description=description)


def registry(
    suite: Any,  # noqa: ANN401 - untyped
    environment: Any,  # noqa: ANN401 - untyped
    suite_name: str,
) -> tuple[Registry, Mapping[str, str]]:
    """Every function of ``suite`` it can express, acting on ``environment``.

    Returns the registry and the functions left out, each with why.
    """
    runtime = FunctionsRuntime(suite.tools)
    entries = []
    refused: dict[str, str] = {}
    for function in suite.tools:
        try:
            spec = spec_for(function, suite_name)
        except UnsupportedFunctionError as err:
            refused[function.name] = str(err)
            continue
        entries.append((spec, implementation(runtime, environment, function)))
    return Registry(entries), refused


def implementation(runtime: Any, environment: Any, function: Any) -> Implementation:  # noqa: ANN401
    """Run ``function`` in ``environment``; its result as JSON would carry it, by alias."""
    adapter = TypeAdapter(function.return_type)
    name = function.name

    def run(**arguments: object) -> object:
        result, error = runtime.run_function(environment, name, arguments)
        if error is not None:
            raise ToolFailedError(name)
        return adapter.dump_python(result, mode="json", by_alias=True)

    return run
