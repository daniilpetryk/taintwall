# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Shared test configuration.

Hypothesis profiles: ``dev`` (default) keeps local runs fast, ``ci`` searches
harder. CI selects it with ``HYPOTHESIS_PROFILE=ci``.
"""

import os

from hypothesis import HealthCheck, settings

settings.register_profile("dev", max_examples=200)
settings.register_profile(
    "ci",
    max_examples=1000,
    derandomize=False,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
