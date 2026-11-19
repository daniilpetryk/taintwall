# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Tools a plan may call: trusted specs, implementations, and the call log.

Modules, from the entry point down (``pyproject.toml`` enforces the order):

* ``registry``: specs with implementations; interpreter tools and a call log;
* ``calls``: the hook that checks arguments and records calls before they run;
* ``spec``: a tool's parameters, result, effects, readers, and signature;
* ``values``, ``schema``: plan values to Python and back; parameter types.
"""

from taintwall.tools.calls import CallLog, CallRecord
from taintwall.tools.registry import DuplicateToolError, Implementation, Registry
from taintwall.tools.schema import (
    ListOf,
    Maybe,
    RecordOf,
    Scalar,
    UnsupportedSchemaError,
    ValueType,
    accepts,
    from_json_schema,
    render,
)
from taintwall.tools.spec import READ_ONLY, ArgumentError, Effects, Param, ToolSpec
from taintwall.tools.values import ConversionError, to_labeled, to_python

__all__ = [
    "READ_ONLY",
    "ArgumentError",
    "CallLog",
    "CallRecord",
    "ConversionError",
    "DuplicateToolError",
    "Effects",
    "Implementation",
    "ListOf",
    "Maybe",
    "Param",
    "RecordOf",
    "Registry",
    "Scalar",
    "ToolSpec",
    "UnsupportedSchemaError",
    "ValueType",
    "accepts",
    "from_json_schema",
    "render",
    "to_labeled",
    "to_python",
]
