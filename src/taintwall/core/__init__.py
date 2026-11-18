# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The trusted core: provenance labels, the plan language and its interpreter.

Import from the subpackage that owns a name, each through its public
``__init__``: ``taintwall.core.labels``, ``taintwall.core.interpreter``, and
the modules ``plan``, ``values``, ``errors`` and ``mint``. The core re-exports
nothing itself, so every name has one import path.
"""
