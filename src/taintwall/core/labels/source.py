# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Sources: where a value can come from.

A source is a kind plus a name. Names are untrusted input (chat handles, tool
names from MCP servers), so they must match a strict ASCII pattern. The owner
is a kind, not a name, and a category (``name=None``) is never parsed from a
string. Precise sources of ``TRUSTED_KINDS`` need the mint's capability token.
"""

import dataclasses
import enum
import re
from collections.abc import Mapping
from typing import Final, Self, override

from taintwall.core.labels.errors import (
    FormatProblem,
    InvalidKindError,
    InvalidSourceError,
    LabelFormatError,
    UnmintedSourceError,
)

MAX_NAME_LENGTH: Final = 128
"""Longest accepted source name."""

_NAME_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]*")
_OWNER_NAME: Final = "owner"


class SourceKind(enum.StrEnum):
    """Where a value can come from."""

    PLAN = "plan"
    """A literal written by the planner, which never sees untrusted data."""

    OWNER = "owner"
    """The wallet owner, identified by config, never by message content."""

    PRINCIPAL = "principal"
    """Anyone else who sends requests or data, such as a group-chat member."""

    TOOL = "tool"
    """A tool response. Whether it is trusted depends on the tool's policy."""

    ADDRESS_BOOK = "address_book"
    """An entry the owner added to the address book."""

    USER_CONFIRMED = "user_confirmed"
    """A value the owner confirmed for one specific call, named by the call ID."""

    VERIFIED_LIST = "verified_list"
    """An entry from a curated list, such as verified token contracts."""

    SYSTEM = "system"
    """Produced by the runtime itself, such as gas estimates or timestamps."""


COLLAPSIBLE_KINDS: Final = frozenset({SourceKind.TOOL, SourceKind.PRINCIPAL, SourceKind.SYSTEM})
"""Kinds whose distinct sources can grow without bound inside loops.

The others are bounded by what the owner curated (address book, verified list,
confirmations) or are singletons (plan, owner), so they always stay precise and
keep their trust however many of them a value depends on.
"""

TRUSTED_KINDS: Final = frozenset(
    {
        SourceKind.PLAN,
        SourceKind.OWNER,
        SourceKind.ADDRESS_BOOK,
        SourceKind.VERIFIED_LIST,
        SourceKind.USER_CONFIRMED,
    }
)
"""Kinds a policy may treat as trusted; only ``taintwall.core.mint`` creates them."""


class _MintToken:
    """Capability that ``taintwall.core.mint`` passes to create trusted sources."""

    __slots__ = ()


_MINT_TOKEN: Final = _MintToken()


@dataclasses.dataclass(frozen=True, slots=True)
class Source:
    """One origin of a value, such as ``tool:read_file`` or ``owner``.

    ``name=None`` marks a category: it stands for every source of that kind
    and is what many sources of one kind collapse into. A category can only be
    created in code (``Source.category``) or loaded from storage, never parsed
    from a name.
    """

    kind: SourceKind
    name: str | None
    _mint: object = dataclasses.field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceKind):
            raise InvalidKindError(self.kind)
        name: object = self.name  # checked at runtime: callers may break the annotation
        if self.kind is SourceKind.OWNER:
            if name != _OWNER_NAME:
                raise InvalidSourceError(self.kind, name)
        elif name is not None and (
            not isinstance(name, str)
            or len(name) > MAX_NAME_LENGTH
            or not _NAME_PATTERN.fullmatch(name)
        ):
            raise InvalidSourceError(self.kind, name)
        if self.kind in TRUSTED_KINDS and name is not None and self._mint is not _MINT_TOKEN:
            raise UnmintedSourceError(self.kind)
        # Never keep the capability on the instance, where anyone could read it.
        object.__setattr__(self, "_mint", None)

    @classmethod
    def principal(cls, identity: str) -> Self:
        """Someone other than the owner, by platform identity (``discord:123``)."""
        return cls(SourceKind.PRINCIPAL, identity)

    @classmethod
    def tool(cls, name: str) -> Self:
        """The response of the tool called ``name``."""
        return cls(SourceKind.TOOL, name)

    @classmethod
    def category(cls, kind: SourceKind) -> Self:
        """Every source of ``kind`` at once. The owner has no category.

        Categories need no minting: they never pass a trust predicate.
        """
        if kind is SourceKind.OWNER:
            raise InvalidSourceError(kind, None)
        return cls(kind, None)

    @property
    def is_category(self) -> bool:
        """Whether this source stands for its whole kind."""
        return self.name is None

    def covers(self, other: Self) -> bool:
        """Whether this source is ``other`` or a category that includes it."""
        return self == other or (self.is_category and self.kind is other.kind)

    def to_json(self) -> dict[str, str | None]:
        """Plain-data form for storage."""
        return {"kind": self.kind.value, "name": self.name}

    @classmethod
    def from_json(cls, data: object, *, _mint: object = None) -> Self:
        """Rebuild a source from ``to_json`` output, validating every field.

        Trusted sources load only through ``taintwall.core.mint``.

        Raises:
            LabelFormatError: for any malformed input.
        """
        if not isinstance(data, Mapping) or set(data) != {"kind", "name"}:
            raise LabelFormatError(FormatProblem.SOURCE_SHAPE)
        kind, name = data["kind"], data["name"]
        if type(kind) is not str or kind not in SourceKind:
            raise LabelFormatError(FormatProblem.SOURCE_KIND)
        if name is not None and type(name) is not str:
            raise LabelFormatError(FormatProblem.SOURCE_NAME_TYPE)
        try:
            return cls(SourceKind(kind), name, _mint)
        except InvalidSourceError as err:
            raise LabelFormatError(FormatProblem.SOURCE_NAME) from err
        except UnmintedSourceError as err:
            raise LabelFormatError(FormatProblem.UNMINTED) from err

    @override
    def __str__(self) -> str:
        if self.kind is SourceKind.OWNER:
            return self.kind.value
        return f"{self.kind.value}:{'*' if self.name is None else self.name}"
