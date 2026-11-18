# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Mint: the only way to create trusted provenance.

Sources of ``TRUSTED_KINDS`` (plan literals, the owner, address-book and
verified-list entries, per-call confirmations) mean "a policy may trust this".
Creating one is a privileged act, so it goes through this module, and the
import contracts in ``pyproject.toml`` forbid the integration layers
(``taintwall.llm``, ``taintwall.tools``, ``taintwall.web3``) from importing it.

This guards against accidental laundering by our own code, such as a tool
adapter labeling its output as a plan literal. It is not a sandbox: the threat
model already trusts runtime code, and in-process Python can always reach
private names.
"""

from taintwall.core.labels import _MINT_TOKEN, Label, Source, SourceKind

_OWNER_NAME = "owner"


class Mint:
    """Creates trusted sources and loads stored labels that contain them."""

    __slots__ = ()

    def plan(self) -> Source:
        """A literal written by the planner."""
        return Source(SourceKind.PLAN, "literal", _MINT_TOKEN)

    def owner(self) -> Source:
        """The wallet owner, identified by config."""
        return Source(SourceKind.OWNER, _OWNER_NAME, _MINT_TOKEN)

    def address_book(self, entry: str) -> Source:
        """An address-book entry the owner added."""
        return Source(SourceKind.ADDRESS_BOOK, entry, _MINT_TOKEN)

    def verified(self, entry: str) -> Source:
        """An entry of a curated list, such as a verified token contract."""
        return Source(SourceKind.VERIFIED_LIST, entry, _MINT_TOKEN)

    def user_confirmed(self, call_id: str) -> Source:
        """A value the owner confirmed for the call ``call_id`` only."""
        return Source(SourceKind.USER_CONFIRMED, call_id, _MINT_TOKEN)

    def source(self, kind: SourceKind, name: str) -> Source:
        """Any source, trusted kinds included, for code that already holds a mint."""
        return Source(kind, name, _MINT_TOKEN)

    def load_label(self, data: object) -> Label:
        """Load a stored label (from the trusted label store), trusted sources included."""
        return Label.from_json(data, _mint=_MINT_TOKEN)
