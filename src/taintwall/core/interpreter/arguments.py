# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The arguments of one call in a plan."""

from collections.abc import Mapping
from typing import NamedTuple

from taintwall.core.labels import Labeled


class Arguments(NamedTuple):
    """Positional and keyword arguments, already evaluated."""

    positional: tuple[Labeled[object], ...]
    keyword: Mapping[str, Labeled[object]]

    @property
    def all(self) -> tuple[Labeled[object], ...]:
        """Every argument, positional first."""
        return (*self.positional, *self.keyword.values())
