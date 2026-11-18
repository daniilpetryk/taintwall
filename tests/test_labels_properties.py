# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Property tests: label joins form a join-semilattice that never loses taint.

These laws are what the interpreter relies on. If any of them broke, the
order in which a plan combines values could change what a policy sees.
"""

from hypothesis import given
from hypothesis import strategies as st

from taintwall.core import Label, Labeled, Readers, Source, SourceKind
from taintwall.core.labels import CATEGORY, MAX_SOURCES_PER_KIND

# A small name pool makes collisions and crowded kinds likely, so collapsing
# into categories is exercised, not just plain unions.
names = st.sampled_from([f"n{i}" for i in range(MAX_SOURCES_PER_KIND + 3)] + [CATEGORY])
sources = st.builds(Source, kind=st.sampled_from(SourceKind), name=names)
readers = st.sampled_from(Readers)
labels = st.builds(
    Label,
    sources=st.frozensets(sources, min_size=1, max_size=3 * MAX_SOURCES_PER_KIND),
    readers=readers,
)


@given(labels, labels)
def test_join_is_commutative(a: Label, b: Label) -> None:
    assert a.join(b) == b.join(a)


@given(labels, labels, labels)
def test_join_is_associative(a: Label, b: Label, c: Label) -> None:
    assert a.join(b).join(c) == a.join(b.join(c))


@given(labels)
def test_join_is_idempotent(a: Label) -> None:
    assert a.join(a) == a


@given(labels, labels)
def test_join_never_loses_taint(a: Label, b: Label) -> None:
    joined = a.join(b)
    assert joined.covers(a)
    assert joined.covers(b)


@given(labels, labels)
def test_join_takes_the_stricter_readers(a: Label, b: Label) -> None:
    # Independent oracle: readers are ordered by value, the join takes the max.
    # Comparing against Readers.meet would only test meet against itself.
    assert a.join(b).readers.value == max(a.readers.value, b.readers.value)


@given(labels)
def test_normalization_is_stable(a: Label) -> None:
    assert Label(a.sources, a.readers) == a


@given(labels)
def test_no_kind_exceeds_the_limit(a: Label) -> None:
    for kind in SourceKind:
        precise = [s for s in a.sources if s.kind is kind and not s.is_category]
        assert len(precise) <= MAX_SOURCES_PER_KIND
        if any(s.kind is kind and s.is_category for s in a.sources):
            assert not precise


@given(labels, labels)
def test_only_from_is_monotone(a: Label, b: Label) -> None:
    # Joining can never turn an untrusted value into a trusted one.
    trusted = {SourceKind.PLAN, SourceKind.ADDRESS_BOOK, SourceKind.USER_CONFIRMED}
    if a.join(b).only_from(trusted):
        assert a.only_from(trusted)
        assert b.only_from(trusted)


@given(labels, st.lists(labels, max_size=4))
def test_derive_label_covers_all_inputs(base: Label, others: list[Label]) -> None:
    value = Labeled(0, base)
    inputs = [Labeled(i, label) for i, label in enumerate(others)]
    derived = value.derive(1, *inputs)
    assert derived.label.covers(base)
    for label in others:
        assert derived.label.covers(label)
