# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Records: named fields, each with its own label."""

import pytest

from taintwall.core import Label, Labeled, Source
from taintwall.core.values import InvalidFieldError, NotLabeledFieldError, Record

TOOL = Label.of(Source.tool("rpc"))


def test_fields_are_sorted_so_equal_records_compare_equal() -> None:
    one = Record.of({"b": Labeled(1, TOOL), "a": Labeled(2, TOOL)})
    two = Record.of({"a": Labeled(2, TOOL), "b": Labeled(1, TOOL)})
    assert one == two
    assert one.names == ("a", "b")
    assert repr(one) == "Record(a, b)"


def test_get_returns_none_for_missing_fields() -> None:
    record = Record.of({"a": Labeled(1, TOOL)})
    assert record.get("a") == Labeled(1, TOOL)
    assert record.get("z") is None


@pytest.mark.parametrize("name", ["", "A", "1a", "_a", "a-b", "a" * 65, "é"])
def test_rejects_bad_field_names(name: str) -> None:
    with pytest.raises(InvalidFieldError):
        Record.of({name: Labeled(1, TOOL)})


def test_rejects_non_string_field_names() -> None:
    with pytest.raises(InvalidFieldError):
        Record(((1, Labeled(1, TOOL)),))  # type: ignore[arg-type]


def test_rejects_duplicate_field_names() -> None:
    with pytest.raises(InvalidFieldError):
        Record((("a", Labeled(1, TOOL)), ("a", Labeled(2, TOOL))))


def test_rejects_unlabeled_values() -> None:
    with pytest.raises(NotLabeledFieldError):
        Record.of({"a": 1})  # type: ignore[dict-item]
