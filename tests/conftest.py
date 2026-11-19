# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Shared test configuration.

Hypothesis profiles: ``dev`` (default) keeps local runs fast, ``ci`` searches
harder and prints a reproduction blob for every failure, since CI keeps no
example database, and ``deep`` searches much harder for the nightly run.
CI selects them with ``HYPOTHESIS_PROFILE``.
"""

import os

import pytest
from hypothesis import HealthCheck, settings

PROFILES = ("dev", "ci", "deep")

settings.register_profile("dev", max_examples=200)
settings.register_profile(
    "ci",
    max_examples=500,
    print_blob=True,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)

settings.register_profile(
    "deep",
    max_examples=5000,
    print_blob=True,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)

_profile = os.environ.get("HYPOTHESIS_PROFILE", "dev")
if _profile not in PROFILES:
    message = f"HYPOTHESIS_PROFILE must be one of {PROFILES}, got {_profile!r}"
    raise pytest.UsageError(message)
settings.load_profile(_profile)
