# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for provenance labels: design cases and attacks on the model."""

import dataclasses

import pytest

from taintwall.core import (
    EmptyLabelError,
    FormatProblem,
    InvalidSourceError,
    Label,
    Labeled,
    LabelFormatError,
    MutableValueError,
    Readers,
    Source,
    SourceKind,
    join_all,
)
from taintwall.core.labels import MAX_NAME_LENGTH, MAX_SOURCES_PER_KIND

INVOICE = Source.tool("read_file")
BALANCE = Source.tool("get_balance")
METADATA = Source.tool("token_metadata")
ALICE = Source(SourceKind.ADDRESS_BOOK, "alice")
TOOL_CATEGORY = Source.category(SourceKind.TOOL)


@dataclasses.dataclass(frozen=True)
class FrozenRecord:
    amount: int
    note: str


@dataclasses.dataclass
class MutableRecord:
    amount: int


class TestSourceNames:
    def test_string_forms(self) -> None:
        assert str(INVOICE) == "tool:read_file"
        assert str(Source.owner()) == "owner"
        assert str(Source.principal("discord:123")) == "principal:discord:123"
        assert str(TOOL_CATEGORY) == "tool:*"

    @pytest.mark.parametrize(
        "name",
        [
            "discord:123",
            "telegram:@alice",
            "mcp/uniswap.quote",
            "eth:0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "a",
            "x" * MAX_NAME_LENGTH,
        ],
    )
    def test_accepts_platform_identities_and_tool_names(self, name: str) -> None:
        assert Source.principal(name).name == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            " ",
            "read file",
            "tab\tname",
            "line\nbreak",
            "*",
            "-leading-dash",
            ".hidden",
            "own" + chr(0x0435) + "r",  # Cyrillic "e": a look-alike of "owner"
            "nul\x00byte",
            "x" * (MAX_NAME_LENGTH + 1),
        ],
    )
    def test_rejects_malformed_names(self, name: str) -> None:
        with pytest.raises(InvalidSourceError):
            Source.principal(name)

    def test_error_message_does_not_echo_huge_input(self) -> None:
        with pytest.raises(InvalidSourceError) as err:
            Source.principal("!" * 10_000)
        assert len(str(err.value)) < 200


class TestOwner:
    def test_a_principal_named_owner_is_not_the_owner(self) -> None:
        # Attack: a stranger whose chat handle is literally "owner".
        stranger = Label.of(Source.principal("owner"))
        assert SourceKind.OWNER not in stranger.kinds
        assert stranger.principals == {"owner"}

    def test_owner_has_a_fixed_identity(self) -> None:
        with pytest.raises(InvalidSourceError):
            Source(SourceKind.OWNER, "someone-else")
        assert Source(SourceKind.OWNER, "owner") == Source.owner()

    def test_owner_has_no_category(self) -> None:
        with pytest.raises(InvalidSourceError):
            Source.category(SourceKind.OWNER)
        with pytest.raises(InvalidSourceError):
            Source(SourceKind.OWNER, None)

    def test_owner_request_with_stranger_data(self) -> None:
        label = Label.of(Source.owner(), Source.principal("discord:666"))
        assert SourceKind.OWNER in label.kinds
        assert label.principals == {"discord:666"}


class TestCategories:
    def test_category_covers_its_kind_only(self) -> None:
        assert TOOL_CATEGORY.covers(INVOICE)
        assert not TOOL_CATEGORY.covers(ALICE)
        assert not INVOICE.covers(TOOL_CATEGORY)

    def test_a_star_handle_cannot_become_a_category(self) -> None:
        # Attack: a handle "*" must not swallow other principals.
        with pytest.raises(InvalidSourceError):
            Source.principal("*")

    def test_crowded_kind_collapses(self) -> None:
        many = Label.of(*(Source.tool(f"t{i}") for i in range(MAX_SOURCES_PER_KIND + 1)))
        assert many.sources == {TOOL_CATEGORY}

    def test_kind_at_the_limit_stays_precise(self) -> None:
        sources = [Source.tool(f"t{i}") for i in range(MAX_SOURCES_PER_KIND)]
        assert Label.of(*sources).sources == frozenset(sources)

    def test_category_absorbs_specific_sources_of_its_kind_only(self) -> None:
        label = Label.of(TOOL_CATEGORY, INVOICE, ALICE)
        assert label.sources == {TOOL_CATEGORY, ALICE}

    def test_collapsed_principals_show_as_star(self) -> None:
        crowd = Label.of(
            *(Source.principal(f"discord:{i}") for i in range(MAX_SOURCES_PER_KIND + 1))
        )
        assert crowd.principals == {"*"}


class TestReaders:
    def test_meet_picks_the_stricter(self) -> None:
        assert Readers.PUBLIC.meet(Readers.USER) is Readers.USER
        assert Readers.USER.meet(Readers.PUBLIC) is Readers.USER
        assert Readers.PUBLIC.meet(Readers.PUBLIC) is Readers.PUBLIC

    def test_join_keeps_the_stricter_readers(self) -> None:
        private = Label.of(ALICE, readers=Readers.USER)
        assert private.join(Label.of(Source.plan())).readers is Readers.USER


class TestTrust:
    TRUSTED_TOOLS = frozenset({"get_balance"})

    def trusted(self, source: Source) -> bool:
        if source.kind is SourceKind.TOOL:
            return source.name in self.TRUSTED_TOOLS
        return source.kind in {SourceKind.PLAN, SourceKind.ADDRESS_BOOK}

    def test_per_source_trust_tells_tools_apart(self) -> None:
        # Design Q2: an RPC balance is trusted, token metadata is not.
        assert Label.of(BALANCE).all_sources(self.trusted)
        assert not Label.of(METADATA).all_sources(self.trusted)
        assert not Label.of(BALANCE, METADATA).all_sources(self.trusted)

    def test_a_category_is_never_trusted(self) -> None:
        def everything(_source: Source) -> bool:
            return True

        assert not Label.of(TOOL_CATEGORY).all_sources(everything)

    def test_only_from_kinds(self) -> None:
        assert Label.of(Source.plan(), ALICE).only_from({SourceKind.PLAN, SourceKind.ADDRESS_BOOK})
        assert not Label.of(INVOICE, Source.plan()).only_from({SourceKind.PLAN})
        assert not Label.of(TOOL_CATEGORY).only_from({SourceKind.TOOL})

    def test_confirmation_is_scoped_to_one_call(self) -> None:
        call_7 = Label.of(Source.user_confirmed("call-7"))

        def confirmed_for_call_8(source: Source) -> bool:
            return source == Source.user_confirmed("call-8")

        assert not call_7.all_sources(confirmed_for_call_8)


class TestLabel:
    def test_needs_a_source(self) -> None:
        with pytest.raises(EmptyLabelError):
            Label.of()

    def test_invoice_example(self) -> None:
        # Design doc: recipient from an untrusted file joined with a plan literal.
        tx = Label.of(INVOICE).join(Label.of(Source.plan()))
        assert tx.sources == {INVOICE, Source.plan()}
        assert not tx.only_from({SourceKind.PLAN, SourceKind.ADDRESS_BOOK})

    def test_string_form_is_sorted_and_readable(self) -> None:
        label = Label.of(Source.plan(), INVOICE, readers=Readers.USER)
        assert str(label) == "{plan:literal, tool:read_file} readers=user"

    def test_join_all(self) -> None:
        a, b, c = Label.of(INVOICE), Label.of(Source.plan()), Label.of(ALICE)
        assert join_all(a, b, c) == a.join(b).join(c)
        assert join_all(a) == a


class TestSerialization:
    def test_round_trip(self) -> None:
        label = Label.of(Source.owner(), INVOICE, TOOL_CATEGORY, ALICE, readers=Readers.USER)
        assert Label.from_json(label.to_json()) == label

    def test_stable_output(self) -> None:
        label = Label.of(ALICE, Source.plan(), Source.owner())
        assert label.to_json() == {
            "sources": [
                {"kind": "address_book", "name": "alice"},
                {"kind": "owner", "name": "owner"},
                {"kind": "plan", "name": "literal"},
            ],
            "readers": "public",
        }

    @pytest.mark.parametrize(
        ("data", "problem"),
        [
            ([], FormatProblem.LABEL_SHAPE),
            ({"sources": []}, FormatProblem.LABEL_SHAPE),
            ({"sources": [], "readers": "public", "x": 1}, FormatProblem.LABEL_SHAPE),
            ({"sources": "tool:x", "readers": "public"}, FormatProblem.SOURCES_TYPE),
            ({"sources": [], "readers": "everyone"}, FormatProblem.READERS),
            ({"sources": ["tool:x"], "readers": "public"}, FormatProblem.SOURCE_SHAPE),
            (
                {"sources": [{"kind": "god", "name": "x"}], "readers": "public"},
                FormatProblem.SOURCE_KIND,
            ),
            (
                {"sources": [{"kind": "tool", "name": 7}], "readers": "public"},
                FormatProblem.SOURCE_NAME_TYPE,
            ),
        ],
    )
    def test_rejects_malformed_data(self, data: object, problem: FormatProblem) -> None:
        with pytest.raises(LabelFormatError) as err:
            Label.from_json(data)
        assert err.value.problem is problem

    def test_validates_names_on_load(self) -> None:
        stored = {"sources": [{"kind": "principal", "name": "*"}], "readers": "public"}
        with pytest.raises(InvalidSourceError):
            Label.from_json(stored)

    def test_empty_source_list_is_rejected(self) -> None:
        with pytest.raises(EmptyLabelError):
            Label.from_json({"sources": [], "readers": "public"})


class TestLabeled:
    def test_derive_joins_every_input(self) -> None:
        amount = Labeled(100, Label.of(INVOICE))
        fee = Labeled(1, Label.of(Source.plan()))
        total = amount.derive(101, fee)
        assert total.value == 101
        assert total.label == Label.of(INVOICE, Source.plan())

    def test_derive_without_inputs_keeps_the_label(self) -> None:
        name = Labeled("alice", Label.of(ALICE, readers=Readers.USER))
        assert name.derive("ALICE").label == name.label

    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            7,
            1.5,
            "text",
            b"bytes",
            (1, ("nested", 2)),
            frozenset({1, 2}),
            FrozenRecord(1, "x"),
            Readers.USER,
        ],
    )
    def test_accepts_immutable_values(self, value: object) -> None:
        assert Labeled(value, Label.of(Source.plan())).value == value

    def test_accepts_per_field_labels(self) -> None:
        # Design Q2: a trusted balance and an untrusted name in one record.
        record = (
            Labeled(100, Label.of(BALANCE)),
            Labeled("USDC", Label.of(METADATA)),
        )
        outer = Labeled(record, Label.of(BALANCE, METADATA))
        assert outer.value[0].label.all_sources(lambda s: s == BALANCE)

    @pytest.mark.parametrize(
        "value",
        [[1, 2], {"a": 1}, {1, 2}, bytearray(b"x"), (1, [2]), MutableRecord(1)],
    )
    def test_rejects_mutable_values(self, value: object) -> None:
        with pytest.raises(MutableValueError):
            Labeled(value, Label.of(Source.plan()))

    def test_derive_also_rejects_mutable_values(self) -> None:
        base = Labeled(1, Label.of(Source.plan()))
        with pytest.raises(MutableValueError):
            base.derive([1])

    def test_is_immutable(self) -> None:
        value = Labeled(1, Label.of(Source.plan()))
        with pytest.raises(dataclasses.FrozenInstanceError):
            value.value = 2  # type: ignore[misc]
