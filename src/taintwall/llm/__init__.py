# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Provider-agnostic LLM client (OpenRouter backend) and on-disk response cache.

Modules, from the entry point down (``pyproject.toml`` enforces the order):

* ``openrouter | cache``: the OpenRouter backend | replies cached by request hash;
* ``client``: what the runtime asks of a model, and the typed errors.

This package cannot import tools, the chain or the store (an import contract):
a model never acts, it only answers.
"""

from taintwall.llm.cache import CachedClient
from taintwall.llm.client import (
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
from taintwall.llm.openrouter import MissingKeyError, OpenRouterClient, Transport, read_key

__all__ = [
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
    "read_key",
]
