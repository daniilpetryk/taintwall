# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for provenance labels: design cases and attacks on the model."""

import collections
import dataclasses
import datetime as dt
import enum
from typing import NamedTuple, override

import pytest

from taintwall.core import (
    EmptyLabelError,
    FormatProblem,
    InvalidKindError,
    InvalidReadersError,
    InvalidSourceError,
    Label,
    Labeled,
    LabelFormatError,
    MutableValueError,
    NotALabelError,
    NotASourceError,
    Readers,
    Source,
    SourceKind,
    ValueTooDeepError,
    join_all,
)
from taintwall.core.labels import (
    MAX_NAME_LENGTH,
    MAX_SOURCES_PER_KIND,
    MAX_VALUE_DEPTH,
    UnmintedSourceError,
)
from taintwall.core.mint import Mint

MINT = Mint()


class TypedPoint(NamedTuple):
    x: int
    y: int


INVOICE = Source.tool("read_file")
BALANCE = Source.tool("get_balance")
METADATA = Source.tool("token_metadata")
ALICE = MINT.address_book("alice")
TOOL_CATEGORY = Source.category(SourceKind.TOOL)
TOOL_X = {"kind": "tool", "name": "x"}


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
        assert str(MINT.owner()) == "owner"
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
            MINT.source(SourceKind.OWNER, "someone-else")
        assert MINT.source(SourceKind.OWNER, "owner") == MINT.owner()

    def test_owner_has_no_category(self) -> None:
        with pytest.raises(InvalidSourceError):
            Source.category(SourceKind.OWNER)
        with pytest.raises(InvalidSourceError):
            Source(SourceKind.OWNER, None)

    def test_owner_request_with_stranger_data(self) -> None:
        label = Label.of(MINT.owner(), Source.principal("discord:666"))
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
        assert private.join(Label.of(MINT.plan())).readers is Readers.USER


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
        assert Label.of(MINT.plan(), ALICE).only_from({SourceKind.PLAN, SourceKind.ADDRESS_BOOK})
        assert not Label.of(INVOICE, MINT.plan()).only_from({SourceKind.PLAN})
        assert not Label.of(TOOL_CATEGORY).only_from({SourceKind.TOOL})

    def test_confirmation_is_scoped_to_one_call(self) -> None:
        call_7 = Label.of(MINT.user_confirmed("call-7"))

        def confirmed_for_call_8(source: Source) -> bool:
            return source == MINT.user_confirmed("call-8")

        assert not call_7.all_sources(confirmed_for_call_8)


class TestLabel:
    def test_needs_a_source(self) -> None:
        with pytest.raises(EmptyLabelError):
            Label.of()

    def test_invoice_example(self) -> None:
        # Design doc: recipient from an untrusted file joined with a plan literal.
        tx = Label.of(INVOICE).join(Label.of(MINT.plan()))
        assert tx.sources == {INVOICE, MINT.plan()}
        assert not tx.only_from({SourceKind.PLAN, SourceKind.ADDRESS_BOOK})

    def test_string_form_is_sorted_and_readable(self) -> None:
        label = Label.of(MINT.plan(), INVOICE, readers=Readers.USER)
        assert str(label) == "{plan:literal, tool:read_file} readers=user"

    def test_join_all(self) -> None:
        a, b, c = Label.of(INVOICE), Label.of(MINT.plan()), Label.of(ALICE)
        assert join_all(a, b, c) == a.join(b).join(c)
        assert join_all(a) == a


class TestSerialization:
    def test_round_trip(self) -> None:
        label = Label.of(MINT.owner(), INVOICE, TOOL_CATEGORY, ALICE, readers=Readers.USER)
        assert MINT.load_label(label.to_json()) == label

    def test_stable_output(self) -> None:
        label = Label.of(ALICE, MINT.plan(), MINT.owner())
        assert label.to_json() == {
            "v": 1,
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
            ({"v": 1, "sources": []}, FormatProblem.LABEL_SHAPE),
            ({"v": 1, "sources": [], "readers": "public", "x": 1}, FormatProblem.LABEL_SHAPE),
            ({"sources": [], "readers": "public"}, FormatProblem.LABEL_SHAPE),
            ({"v": 2, "sources": [], "readers": "public"}, FormatProblem.VERSION),
            ({"v": True, "sources": [], "readers": "public"}, FormatProblem.VERSION),
            ({"v": "1", "sources": [], "readers": "public"}, FormatProblem.VERSION),
            ({"v": 1, "sources": "tool:x", "readers": "public"}, FormatProblem.SOURCES_TYPE),
            ({"v": 1, "sources": [], "readers": "public"}, FormatProblem.NO_SOURCES),
            ({"v": 1, "sources": [TOOL_X], "readers": "everyone"}, FormatProblem.READERS),
            ({"v": 1, "sources": [TOOL_X], "readers": 1}, FormatProblem.READERS),
            ({"v": 1, "sources": ["tool:x"], "readers": "public"}, FormatProblem.SOURCE_SHAPE),
            (
                {"v": 1, "sources": [{"kind": "god", "name": "x"}], "readers": "public"},
                FormatProblem.SOURCE_KIND,
            ),
            (
                {"v": 1, "sources": [{"kind": "tool", "name": 7}], "readers": "public"},
                FormatProblem.SOURCE_NAME_TYPE,
            ),
            (
                {"v": 1, "sources": [{"kind": "tool", "name": "a b"}], "readers": "public"},
                FormatProblem.SOURCE_NAME,
            ),
            (
                {"v": 1, "sources": [{"kind": "plan", "name": "literal"}], "readers": "public"},
                FormatProblem.UNMINTED,
            ),
            (
                {"v": 1, "sources": [{"kind": "owner", "name": "owner"}], "readers": "public"},
                FormatProblem.UNMINTED,
            ),
        ],
    )
    def test_rejects_malformed_data(self, data: object, problem: FormatProblem) -> None:
        with pytest.raises(LabelFormatError) as err:
            Label.from_json(data)
        assert err.value.problem is problem

    def test_validates_names_on_load(self) -> None:
        stored = {"v": 1, "sources": [{"kind": "principal", "name": "*"}], "readers": "public"}
        with pytest.raises(LabelFormatError) as err:
            Label.from_json(stored)
        assert err.value.problem is FormatProblem.SOURCE_NAME


class TestStrictTypes:
    """Wrongly typed constructor input must fail loudly, never be stored."""

    def test_a_string_kind_cannot_spoof_the_owner(self) -> None:
        # Attack: "owner" == SourceKind.OWNER for a StrEnum, so a plain string
        # kind would slip past the owner check and still look like the owner.
        with pytest.raises(InvalidKindError):
            Source("owner", "attacker")  # type: ignore[arg-type]

    def test_a_string_kind_is_rejected(self) -> None:
        with pytest.raises(InvalidKindError):
            Source("principal", "discord:1")  # type: ignore[arg-type]

    def test_a_non_string_name_is_rejected(self) -> None:
        with pytest.raises(InvalidSourceError):
            Source(SourceKind.TOOL, 7)  # type: ignore[arg-type]

    def test_a_mutable_set_is_copied_not_shared(self) -> None:
        given = {MINT.plan()}
        label = Label(given)  # type: ignore[arg-type]
        given.add(INVOICE)
        assert label.sources == {MINT.plan()}
        assert isinstance(label.sources, frozenset)
        assert hash(label) == hash(Label.of(MINT.plan()))

    def test_string_readers_are_rejected(self) -> None:
        with pytest.raises(InvalidReadersError):
            Label.of(MINT.plan(), readers="user")  # type: ignore[arg-type]

    def test_non_source_items_are_rejected(self) -> None:
        with pytest.raises(NotASourceError):
            Label(frozenset({"tool:read_file"}))  # type: ignore[arg-type]

    def test_labeled_needs_a_label(self) -> None:
        with pytest.raises(NotALabelError):
            Labeled(1, "plan")  # type: ignore[arg-type]


class TestCollapsibleKinds:
    def test_curated_kinds_never_collapse(self) -> None:
        # A rebalance over many verified tokens must stay trusted.
        tokens = [MINT.verified(f"token-{i}") for i in range(20)]
        label = Label.of(*tokens)
        assert label.sources == frozenset(tokens)
        assert label.only_from({SourceKind.VERIFIED_LIST})

    def test_principals_still_collapse(self) -> None:
        crowd = [Source.principal(f"discord:{i}") for i in range(MAX_SOURCES_PER_KIND + 1)]
        assert Label.of(*crowd).sources == {Source.category(SourceKind.PRINCIPAL)}


class TestLabeled:
    def test_derive_joins_every_input(self) -> None:
        amount = Labeled(100, Label.of(INVOICE))
        fee = Labeled(1, Label.of(MINT.plan()))
        total = amount.derive(101, fee)
        assert total.value == 101
        assert total.label == Label.of(INVOICE, MINT.plan())

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
        assert Labeled(value, Label.of(MINT.plan())).value == value

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
            Labeled(value, Label.of(MINT.plan()))

    def test_rejects_enum_members_with_mutable_values(self) -> None:
        class Shared(enum.Enum):
            ITEMS = [1]  # noqa: RUF012

        with pytest.raises(MutableValueError):
            Labeled(Shared.ITEMS, Label.of(MINT.plan()))

    def test_accepts_enum_members_with_immutable_values(self) -> None:
        assert Labeled(Readers.USER, Label.of(MINT.plan())).value is Readers.USER

    def test_rejects_subclasses_of_atoms(self) -> None:
        class Tagged(str):
            __slots__ = ("tags",)

        with pytest.raises(MutableValueError):
            Labeled(Tagged("hi"), Label.of(MINT.plan()))

    def test_rejects_too_deep_nesting(self) -> None:
        deep: tuple[object, ...] = ()
        for _ in range(MAX_VALUE_DEPTH + 1):
            deep = (deep,)
        with pytest.raises(ValueTooDeepError):
            Labeled(deep, Label.of(MINT.plan()))

    def test_survives_attacker_depth_without_recursion_error(self) -> None:
        deep: tuple[object, ...] = ()
        for _ in range(10_000):
            deep = (deep,)
        with pytest.raises(ValueTooDeepError):
            Labeled(deep, Label.of(MINT.plan()))

    def test_accepts_depth_at_the_limit(self) -> None:
        deep: tuple[object, ...] = ()
        for _ in range(MAX_VALUE_DEPTH):
            deep = (deep,)
        assert Labeled(deep, Label.of(MINT.plan())).value == deep

    def test_datetimes_need_a_fixed_offset(self) -> None:
        naive = dt.datetime(2026, 11, 18, 12, 0)  # noqa: DTZ001
        utc = dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.UTC)
        assert Labeled(naive, Label.of(MINT.plan())).value == naive
        assert Labeled(utc, Label.of(MINT.plan())).value == utc

        class MutableZone(dt.tzinfo):
            @override
            def utcoffset(self, _when: dt.datetime | None) -> dt.timedelta:
                return dt.timedelta(0)

            @override
            def dst(self, _when: dt.datetime | None) -> dt.timedelta:
                return dt.timedelta(0)

            @override
            def tzname(self, _when: dt.datetime | None) -> str:
                return "MUT"

        with pytest.raises(MutableValueError):
            Labeled(dt.datetime(2026, 1, 1, tzinfo=MutableZone()), Label.of(MINT.plan()))

    def test_nested_labeled_values_are_not_rescanned(self) -> None:
        inner = Labeled(tuple(range(1000)), Label.of(BALANCE))
        outer = Labeled((inner,) * 1000, Label.of(BALANCE))
        assert len(outer.value) == 1000

    def test_derive_also_rejects_mutable_values(self) -> None:
        base = Labeled(1, Label.of(MINT.plan()))
        with pytest.raises(MutableValueError):
            base.derive([1])

    def test_is_immutable(self) -> None:
        value = Labeled(1, Label.of(MINT.plan()))
        with pytest.raises(dataclasses.FrozenInstanceError):
            value.value = 2  # type: ignore[misc]


class TestMinting:
    """Trusted provenance can only come from taintwall.core.mint."""

    @pytest.mark.parametrize(
        ("kind", "name"),
        [
            (SourceKind.PLAN, "literal"),
            (SourceKind.OWNER, "owner"),
            (SourceKind.ADDRESS_BOOK, "alice"),
            (SourceKind.VERIFIED_LIST, "usdc"),
            (SourceKind.USER_CONFIRMED, "call-1"),
        ],
    )
    def test_trusted_sources_need_the_mint(self, kind: SourceKind, name: str) -> None:
        # Attack: a tool adapter labeling its output as a plan literal.
        with pytest.raises(UnmintedSourceError):
            Source(kind, name)

    def test_untrusted_sources_need_no_mint(self) -> None:
        assert Source.tool("read_file").kind is SourceKind.TOOL
        assert Source.principal("discord:1").kind is SourceKind.PRINCIPAL

    def test_trusted_categories_need_no_mint(self) -> None:
        # A category never passes a trust predicate, so it grants nothing.
        category = Source.category(SourceKind.ADDRESS_BOOK)
        assert not Label.of(category).only_from({SourceKind.ADDRESS_BOOK})

    def test_the_capability_is_not_kept_on_the_source(self) -> None:
        assert MINT.plan()._mint is None  # noqa: SLF001

    def test_replace_cannot_re_mint(self) -> None:
        with pytest.raises(UnmintedSourceError):
            dataclasses.replace(MINT.plan(), name="other")

    def test_public_loading_rejects_trusted_sources(self) -> None:
        stored = Label.of(MINT.plan()).to_json()
        with pytest.raises(LabelFormatError) as err:
            Label.from_json(stored)
        assert err.value.problem is FormatProblem.UNMINTED
        assert MINT.load_label(stored) == Label.of(MINT.plan())

    def test_public_loading_accepts_untrusted_sources(self) -> None:
        label = Label.of(INVOICE, Source.principal("discord:1"))
        assert Label.from_json(label.to_json()) == label


class TestThirdReview:
    def test_only_from_rejects_string_kinds(self) -> None:
        with pytest.raises(InvalidKindError):
            Label.of(INVOICE).only_from({"tool"})  # type: ignore[arg-type]

    def test_a_hostile_repr_keeps_the_typed_error(self) -> None:
        class Hostile:
            @override
            def __repr__(self) -> str:
                raise RuntimeError

        with pytest.raises(InvalidKindError):
            Source(Hostile(), "x")  # type: ignore[arg-type]

    def test_unhashable_sources_give_a_typed_error(self) -> None:
        with pytest.raises(NotASourceError):
            Label([[1]])  # type: ignore[arg-type]

    def test_namedtuples_are_immutable(self) -> None:
        point = collections.namedtuple("point", "x y")  # noqa: PYI024
        assert Labeled(point(1, 2), Label.of(MINT.plan())).value == (1, 2)
        assert Labeled(TypedPoint(1, 2), Label.of(MINT.plan())).value.x == 1

    def test_tuple_subclasses_with_state_are_rejected(self) -> None:
        # Python forbids non-empty __slots__ on tuple subclasses, so instance
        # state can only live in a __dict__: such subclasses are rejected.
        class Tagged(tuple[int, ...]):  # noqa: SLOT001
            pass

        tagged = Tagged((1,))
        with pytest.raises(MutableValueError):
            Labeled(tagged, Label.of(MINT.plan()))

    def test_hidden_dataclass_state_is_rejected(self) -> None:
        @dataclasses.dataclass(frozen=True)
        class Sneaky:
            n: int

            def __post_init__(self) -> None:
                object.__setattr__(self, "cache", [])

        with pytest.raises(MutableValueError):
            Labeled(Sneaky(1), Label.of(MINT.plan()))

    def test_equality_keeps_value_types_apart(self) -> None:
        label = Label.of(MINT.plan())
        one, one_float = Labeled(1, label), Labeled(1.0, label)
        true = Labeled(value=True, label=label)
        assert one != true
        assert one != one_float
        assert len({one, true, one_float}) == 3
        assert Labeled(1, label) == one
        assert hash(Labeled(1, label)) == hash(one)
        assert one != 1
