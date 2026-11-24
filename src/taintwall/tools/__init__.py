# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Tools a plan may call: trusted specs, implementations, and the call log.

Modules, from the entry point down (``pyproject.toml`` enforces the order):

* ``reader``: ``query_reader``, the Q-LLM as a tool: data in, a fixed schema out;
* ``registry``: specs with implementations; interpreter tools and a call log;
* ``check``: the static checker: a plan's sure mistakes and its calls, before it runs;
* ``calls``: the hook that checks arguments and records calls before they run;
* ``signatures``, ``scope``: what builtins and methods take; which reads come too early;
* ``infer``: what a plan's values can be, without running it;
* ``spec``: a tool's parameters, result, effects, readers, and signature;
* ``values``, ``schema``: plan values to Python and back; parameter types.
"""

from taintwall.tools.calls import CallLog, CallRecord
from taintwall.tools.check import MAX_ERRORS, PlanCheck, PlannedCall, check_plan
from taintwall.tools.infer import Known, may_fit
from taintwall.tools.reader import (
    NotEnoughInfoError,
    ReaderSchemaError,
    ReplyTypeError,
    reader,
)
from taintwall.tools.registry import (
    DuplicateToolError,
    ForeignLogError,
    Implementation,
    RecordClashError,
    Registry,
    ResultTypeError,
)
from taintwall.tools.schema import (
    ListOf,
    Maybe,
    OneOf,
    RecordOf,
    Scalar,
    UnsupportedSchemaError,
    ValueType,
    accepts,
    from_json_schema,
    records_in,
    render,
    render_record,
)
from taintwall.tools.spec import (
    READ_ONLY,
    ArgumentError,
    Effects,
    InvalidParamError,
    Param,
    ToolSpec,
)
from taintwall.tools.values import (
    ConversionError,
    label_plain,
    normalize,
    to_labeled,
    to_python,
)

__all__ = [
    "MAX_ERRORS",
    "READ_ONLY",
    "ArgumentError",
    "CallLog",
    "CallRecord",
    "ConversionError",
    "DuplicateToolError",
    "Effects",
    "ForeignLogError",
    "Implementation",
    "InvalidParamError",
    "Known",
    "ListOf",
    "Maybe",
    "NotEnoughInfoError",
    "OneOf",
    "Param",
    "PlanCheck",
    "PlannedCall",
    "ReaderSchemaError",
    "RecordClashError",
    "RecordOf",
    "Registry",
    "ReplyTypeError",
    "ResultTypeError",
    "Scalar",
    "ToolSpec",
    "UnsupportedSchemaError",
    "ValueType",
    "accepts",
    "check_plan",
    "from_json_schema",
    "label_plain",
    "may_fit",
    "normalize",
    "reader",
    "records_in",
    "render",
    "render_record",
    "to_labeled",
    "to_python",
]
