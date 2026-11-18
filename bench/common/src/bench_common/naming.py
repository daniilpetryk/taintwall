# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""How attacks address a model in their text (``important_instructions``)."""

_PROSE_NAMES = {
    "anthropic": "Claude",
    "openai": "GPT-4",
    "google": "Gemini",
    "meta-llama": "Llama",
    "cohere": "Command R+",
}


def prose_name(model: str) -> str:
    """How an attack addresses ``model`` (``provider/name``) in its text."""
    provider = model.split("/", 1)[0]
    return _PROSE_NAMES.get(provider, "AI assistant")
