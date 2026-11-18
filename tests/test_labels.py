# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for provenance labels: concrete cases from the design."""

import dataclasses

import pytest

from taintwall.core import (
    OWNER,
    EmptyLabelError,
    InvalidSourceError,
    Label,
    Labeled,
    Readers,
    Source,
    SourceKind,
    join_all,
)
from taintwall.core.labels import MAX_SOURCES_PER_KIND

INVOICE = Source.tool("read_file")
ADDRESS_BOOK = Source(SourceKind.ADDRESS_BOOK, "alice")


class TestSource:
    def test_string_form(self) -> None:
        assert str(INVOICE) == "tool:read_file"
        assert str(Source.principal(OWNER)) == "principal:owner"

    @pytest.mark.parametrize("name", ["", " ", "read file", "tab\tname"])
    def test_rejects_empty_or_whitespace_names(self, name: str) -> None:
        with pytest.raises(InvalidSourceError):
            Source(SourceKind.TOOL, name)

    def test_category_covers_its_kind_only(self) -> None:
        tools = Source.category(SourceKind.TOOL)
        assert tools.covers(INVOICE)
        assert not tools.covers(ADDRESS_BOOK)
        assert not INVOICE.covers(tools)

    def test_is_immutable(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            INVOICE.name = "other"  # type: ignore[misc]


class TestReaders:
    def test_meet_picks_the_stricter(self) -> None:
        assert Readers.PUBLIC.meet(Readers.USER) is Readers.USER
        assert Readers.USER.meet(Readers.PUBLIC) is Readers.USER
        assert Readers.PUBLIC.meet(Readers.PUBLIC) is Readers.PUBLIC


class TestLabel:
    def test_needs_a_source(self) -> None:
        with pytest.raises(EmptyLabelError):
            Label.of()

    def test_invoice_example(self) -> None:
        # Design doc: recipient from an untrusted file joined with a plan literal.
        recipient = Label.of(INVOICE)
        token = Label.of(Source.plan())
        tx = recipient.join(token)
        assert tx.sources == {INVOICE, Source.plan()}
        assert not tx.only_from({SourceKind.PLAN, SourceKind.ADDRESS_BOOK})
        assert token.only_from({SourceKind.PLAN})

    def test_join_keeps_the_stricter_readers(self) -> None:
        private = Label.of(ADDRESS_BOOK, readers=Readers.USER)
        public = Label.of(Source.plan())
        assert private.join(public).readers is Readers.USER

    def test_principals(self) -> None:
        label = Label.of(Source.principal("@stranger"), Source.plan())
        assert label.principals == {"@stranger"}

    def test_crowded_kind_collapses_to_a_category(self) -> None:
        many = Label.of(*(Source.tool(f"t{i}") for i in range(MAX_SOURCES_PER_KIND + 1)))
        assert many.sources == {Source.category(SourceKind.TOOL)}

    def test_kind_at_the_limit_stays_precise(self) -> None:
        sources = [Source.tool(f"t{i}") for i in range(MAX_SOURCES_PER_KIND)]
        assert Label.of(*sources).sources == frozenset(sources)

    def test_category_absorbs_specific_sources(self) -> None:
        label = Label.of(Source.category(SourceKind.TOOL), INVOICE, ADDRESS_BOOK)
        assert label.sources == {Source.category(SourceKind.TOOL), ADDRESS_BOOK}

    def test_collapse_does_not_touch_other_kinds(self) -> None:
        tools = [Source.tool(f"t{i}") for i in range(MAX_SOURCES_PER_KIND + 1)]
        label = Label.of(*tools, ADDRESS_BOOK)
        assert ADDRESS_BOOK in label.sources

    def test_string_form_is_sorted_and_readable(self) -> None:
        label = Label.of(Source.plan(), INVOICE, readers=Readers.USER)
        assert str(label) == "{plan:literal, tool:read_file} readers=user"

    def test_join_all(self) -> None:
        a, b, c = Label.of(INVOICE), Label.of(Source.plan()), Label.of(ADDRESS_BOOK)
        assert join_all(a, b, c) == a.join(b).join(c)
        assert join_all(a) == a


class TestLabeled:
    def test_derive_joins_every_input(self) -> None:
        amount = Labeled(100, Label.of(INVOICE))
        fee = Labeled(1, Label.of(Source.plan()))
        total = amount.derive(101, fee)
        assert total.value == 101
        assert total.label == Label.of(INVOICE, Source.plan())

    def test_derive_without_inputs_keeps_the_label(self) -> None:
        name = Labeled("alice", Label.of(ADDRESS_BOOK, readers=Readers.USER))
        upper = name.derive("ALICE")
        assert upper.label == name.label
