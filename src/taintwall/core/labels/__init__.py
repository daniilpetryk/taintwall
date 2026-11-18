# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Provenance labels: where a value came from and who may see it.

Every value the interpreter handles is a ``Labeled[T]``: the value plus a
``Label``. A label records two things:

* ``sources``: every origin the value may depend on (a tool response, a plan
  literal, the address book, the owner or another person in a chat). Trust is
  not stored on the label; policies compute it from the sources (decision Q1).
* ``readers``: who may see or receive the value (decision Q9).

Combining values joins their labels. A join never makes a label cleaner than
its inputs: sources only accumulate and readers only become more restrictive.

Everything here is validated strictly, because the inputs are partly
attacker-controlled (chat handles, tool names from MCP servers, tool output):

* kinds must be ``SourceKind`` members and readers ``Readers`` members; plain
  strings are rejected, since ``"owner"`` would otherwise compare equal to
  ``SourceKind.OWNER``;
* names must match a strict ASCII pattern; no string becomes the owner or a
  category, which are expressed by the kind and by ``name=None``;
* labeled values must be immutable, checked by exact type and with a depth
  limit, so a value cannot change behind its label;
* precise sources of trusted kinds (``TRUSTED_KINDS``) can only be created
  through ``taintwall.core.mint``, which the integration layers (llm, tools,
  web3) are forbidden to import. Untrusted code can still describe where its
  data came from, but it cannot create trusted provenance out of nothing.

Modules: ``source`` (kinds and sources), ``label`` (labels, joins, storage),
``value`` (``Labeled`` values) and ``errors``.
"""

from taintwall.core.labels.errors import (
    EmptyLabelError,
    FormatProblem,
    InvalidKindError,
    InvalidReadersError,
    InvalidSourceError,
    LabelFormatError,
    MutableValueError,
    NonFiniteNumberError,
    NotALabelError,
    NotASourceError,
    UnmintedSourceError,
    ValueTooDeepError,
)
from taintwall.core.labels.label import (
    LABEL_FORMAT_VERSION,
    MAX_SOURCES_PER_KIND,
    Label,
    Readers,
    join_all,
)
from taintwall.core.labels.source import MAX_NAME_LENGTH, TRUSTED_KINDS, Source, SourceKind
from taintwall.core.labels.value import MAX_VALUE_DEPTH, Labeled

__all__ = [
    "LABEL_FORMAT_VERSION",
    "MAX_NAME_LENGTH",
    "MAX_SOURCES_PER_KIND",
    "MAX_VALUE_DEPTH",
    "TRUSTED_KINDS",
    "EmptyLabelError",
    "FormatProblem",
    "InvalidKindError",
    "InvalidReadersError",
    "InvalidSourceError",
    "Label",
    "LabelFormatError",
    "Labeled",
    "MutableValueError",
    "NonFiniteNumberError",
    "NotALabelError",
    "NotASourceError",
    "Readers",
    "Source",
    "SourceKind",
    "UnmintedSourceError",
    "ValueTooDeepError",
    "join_all",
]
