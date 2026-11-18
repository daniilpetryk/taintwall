# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Label-model behavior that mutation testing found unpinned.

Error messages are part of the contract here: they must name the offending
input, through a bounded preview, so a log shows what was rejected without
letting a hostile value flood it.
"""

import ast
import dataclasses
import datetime as dt
import enum
from collections.abc import Callable
from decimal import Decimal
from typing import NamedTuple, override

import pytest

from taintwall.core.errors import PlanError, PlanErrorCode
from taintwall.core.labels import (
    EmptyLabelError,
    InvalidKindError,
    InvalidReadersError,
    InvalidSourceError,
    Label,
    Labeled,
    LabelFormatError,
    MutableValueError,
    NonFiniteNumberError,
    NotALabelError,
    NotASourceError,
    Readers,
    Source,
    SourceKind,
    UnmintedSourceError,
    ValueTooDeepError,
)
from taintwall.core.labels.errors import preview
from taintwall.core.mint import Mint
from taintwall.core.plan import MAX_PLAN_DEPTH, parse_plan

MINT = Mint()
TOOL = Source.tool("rpc")


class _Zone(dt.tzinfo):
    """A custom time zone: mutable in principle, so rejected."""

    @override
    def utcoffset(self, _when: dt.datetime | None) -> dt.timedelta:
        return dt.timedelta(0)

    @override
    def dst(self, _when: dt.datetime | None) -> dt.timedelta:
        return dt.timedelta(0)

    @override
    def tzname(self, _when: dt.datetime | None) -> str:
        return "zone"


@dataclasses.dataclass(frozen=True)
class _Frozen:
    x: int


def _hidden_state() -> _Frozen:
    item = _Frozen(1)
    object.__setattr__(item, "extra", [1])
    return item


class _Pair(NamedTuple):
    left: object
    right: object


class _Color(enum.Enum):
    RED = 1


def _bad_enum() -> object:
    member = _Color.RED
    object.__setattr__(member, "extra", [1])
    try:
        return Labeled(member, Label.of(TOOL))
    finally:
        object.__delattr__(member, "extra")


@pytest.mark.parametrize(
    ("make", "error", "shown"),
    [
        (lambda: Source(SourceKind.TOOL, "bad name!"), InvalidSourceError, ["tool", "bad name!"]),
        (lambda: Source(SourceKind.OWNER, "alice"), InvalidSourceError, ["owner", "alice"]),
        (lambda: Source.category(SourceKind.OWNER), InvalidSourceError, ["owner"]),
        (lambda: Source("tool", "x"), InvalidKindError, ["'tool'"]),  # type: ignore[arg-type]
        (lambda: Source(SourceKind.PLAN, "literal"), UnmintedSourceError, ["plan"]),
        (lambda: Label(frozenset()), EmptyLabelError, ["a label needs at least one source"]),
        (lambda: Label(5), NotASourceError, ["5"]),  # type: ignore[arg-type]
        (lambda: Label(frozenset({"tool:x"})), NotASourceError, ["'tool:x'"]),  # type: ignore[arg-type]
        (lambda: Label.of(TOOL, readers="user"), InvalidReadersError, ["'user'"]),  # type: ignore[arg-type]
        (lambda: Label.of(TOOL).only_from({"tool"}), InvalidKindError, ["'tool'"]),  # type: ignore[arg-type]
        (lambda: Labeled([1], Label.of(TOOL)), MutableValueError, ["list"]),
        (lambda: Labeled(float("nan"), Label.of(TOOL)), NonFiniteNumberError, ["nan"]),
        (lambda: Labeled(complex("inf"), Label.of(TOOL)), NonFiniteNumberError, ["inf"]),
        (lambda: Labeled(Decimal("NaN"), Label.of(TOOL)), NonFiniteNumberError, ["NaN"]),
        (
            lambda: Labeled(dt.datetime(2026, 1, 1, tzinfo=_Zone()), Label.of(TOOL)),
            MutableValueError,
            ["_Zone"],
        ),
        (lambda: Labeled(_hidden_state(), Label.of(TOOL)), MutableValueError, ["_Frozen"]),
        (lambda: Labeled(1, "label"), NotALabelError, ["'label'"]),  # type: ignore[arg-type]
        (_bad_enum, MutableValueError, ["_Color"]),
    ],
)
def test_errors_name_the_rejected_input(
    make: Callable[[], object], error: type[Exception], shown: list[str]
) -> None:
    with pytest.raises(error) as err:
        make()
    assert all(text in str(err.value) for text in shown), str(err.value)


def test_empty_label_message_is_exact() -> None:
    with pytest.raises(EmptyLabelError) as err:
        Label(frozenset())
    assert str(err.value) == "a label needs at least one source"


def test_too_deep_names_the_limit() -> None:
    value: object = 1
    for _ in range(100):
        value = (value,)
    with pytest.raises(ValueTooDeepError, match="64 levels"):
        Labeled(value, Label.of(TOOL))


def test_previews_are_bounded() -> None:
    assert preview("x" * 1000) != repr("x" * 1000)
    assert len(preview("x" * 1000)) <= 64
    assert preview(5) == "5"


def test_format_errors_say_what_is_wrong() -> None:
    with pytest.raises(LabelFormatError, match="unknown readers"):
        Label.from_json({"v": 1, "sources": [TOOL.to_json()], "readers": "nobody"})


@pytest.mark.parametrize("readers", [["user"], 1, None, {"user": 1}])
def test_non_string_readers_are_a_format_error(readers: object) -> None:
    with pytest.raises(LabelFormatError):
        Label.from_json({"v": 1, "sources": [TOOL.to_json()], "readers": readers})


@pytest.mark.parametrize("data", [["kind", "name"], ("kind", "name"), "kind", 5])
def test_non_mapping_sources_are_a_format_error(data: object) -> None:
    with pytest.raises(LabelFormatError):
        Source.from_json(data)


def test_covers_needs_both_sources_and_readers() -> None:
    public = Label.of(TOOL)
    private = Label.of(TOOL, readers=Readers.USER)
    other = Label.of(Source.tool("other"), readers=Readers.USER)
    assert private.covers(public)
    assert not public.covers(private)
    assert not other.covers(public)  # stricter readers, but not the same sources


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (Readers.PUBLIC, Readers.PUBLIC, Readers.PUBLIC),
        (Readers.PUBLIC, Readers.USER, Readers.USER),
        (Readers.USER, Readers.PUBLIC, Readers.USER),
        (Readers.USER, Readers.USER, Readers.USER),
    ],
)
def test_readers_meet(left: Readers, right: Readers, expected: Readers) -> None:
    assert left.meet(right) is expected


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ((1,), (True,)),
        ((1,), (1.0,)),
        ((0, (1,)), (0, (True,))),
        (_Pair(1, 2), _Pair(True, 2)),  # noqa: FBT003 - the bool is the point
    ],
)
def test_equality_keeps_types_apart_inside_tuples(one: object, other: object) -> None:
    label = Label.of(TOOL)
    assert Labeled(one, label) != Labeled(other, label)
    assert Labeled(one, label) == Labeled(one, label)


def test_siblings_of_a_nested_labeled_value_are_still_checked() -> None:
    # A nested Labeled value is skipped as already checked; the scan must go
    # on with the other elements, whatever order they are visited in.
    inner = Labeled(1, Label.of(TOOL))
    for value in (([1], inner), (inner, [1])):
        with pytest.raises(MutableValueError):
            Labeled(value, Label.of(TOOL))


def test_dataclass_classes_are_not_values() -> None:
    Labeled(_Frozen(1), Label.of(TOOL))
    with pytest.raises(MutableValueError):
        Labeled(_Frozen, Label.of(TOOL))


class TestParserEdges:
    def test_value_error_from_the_parser_is_a_syntax_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(_source: str) -> ast.Module:
            raise ValueError

        monkeypatch.setattr(ast, "parse", broken)
        with pytest.raises(PlanError) as err:
            parse_plan("x = 1")
        assert (err.value.code, err.value.line) == (PlanErrorCode.SYNTAX, None)

    def test_depth_error_on_a_node_without_line_uses_its_parent(self) -> None:
        # Name at the limit, its Load context one deeper; Load has no line.
        source = "\nx = " + "-" * (MAX_PLAN_DEPTH - 2) + "a"
        with pytest.raises(PlanError) as err:
            parse_plan(source)
        assert (err.value.code, err.value.line) == (PlanErrorCode.TOO_DEEP, 2)
