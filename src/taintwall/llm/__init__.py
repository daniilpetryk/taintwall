# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Provider-agnostic LLM client (OpenRouter backend) and on-disk response cache.

Modules, from the entry point down (``pyproject.toml`` enforces the order):

* ``openrouter | cache | budget``: the OpenRouter backend | replies cached
  by request hash | a ceiling on calls and cost;
* ``conform | client``: whether a reply follows its schema | what the runtime
  asks of a model, and the typed errors.

This package cannot import tools, the chain or the store (an import contract):
a model never acts, it only answers.
"""

from taintwall.llm.budget import BudgetedClient
from taintwall.llm.cache import CachedClient
from taintwall.llm.client import (
    BudgetExceededError,
    Json,
    LLMClient,
    LLMError,
    MalformedReplyError,
    ProviderError,
    StructuredReply,
    StructuredRequest,
    TruncatedError,
    Usage,
)
from taintwall.llm.conform import conforms
from taintwall.llm.openrouter import MissingKeyError, OpenRouterClient, Transport, read_key

__all__ = [
    "BudgetExceededError",
    "BudgetedClient",
    "CachedClient",
    "Json",
    "LLMClient",
    "LLMError",
    "MalformedReplyError",
    "MissingKeyError",
    "OpenRouterClient",
    "ProviderError",
    "StructuredReply",
    "StructuredRequest",
    "Transport",
    "TruncatedError",
    "Usage",
    "conforms",
    "read_key",
]
