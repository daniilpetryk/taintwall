# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Property tests: label joins form a join-semilattice that never loses taint.

These laws are what the interpreter relies on. If any of them broke, the
order in which a plan combines values could change what a policy sees.
"""

import contextlib
import json

from hypothesis import given
from hypothesis import strategies as st

from taintwall.core import (
    EmptyLabelError,
    InvalidKindError,
    InvalidReadersError,
    InvalidSourceError,
    Label,
    Labeled,
    LabelFormatError,
    NotASourceError,
    Readers,
    Source,
    SourceKind,
)
from taintwall.core.labels import MAX_SOURCES_PER_KIND, TRUSTED_KINDS, UnmintedSourceError
from taintwall.core.mint import Mint

MINT = Mint()

# A small name pool makes collisions and crowded kinds likely, so collapsing
# into categories is exercised, not just plain unions.
NAMES = [f"n{i}" for i in range(MAX_SOURCES_PER_KIND + 3)]
NON_OWNER_KINDS = [kind for kind in SourceKind if kind is not SourceKind.OWNER]

sources = st.one_of(
    st.just(MINT.owner()),
    st.builds(MINT.source, kind=st.sampled_from(NON_OWNER_KINDS), name=st.sampled_from(NAMES)),
    st.builds(Source.category, st.sampled_from(NON_OWNER_KINDS)),
)
readers = st.sampled_from(Readers)
labels = st.builds(
    Label,
    sources=st.frozensets(sources, min_size=1, max_size=3 * MAX_SOURCES_PER_KIND),
    readers=readers,
)
COLLAPSIBLE = {SourceKind.TOOL, SourceKind.PRINCIPAL, SourceKind.SYSTEM}
POLICY_TRUSTED = {SourceKind.PLAN, SourceKind.OWNER, SourceKind.ADDRESS_BOOK}

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
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
def test_no_collapsible_kind_exceeds_the_limit(a: Label) -> None:
    for kind in SourceKind:
        precise = [s for s in a.sources if s.kind is kind and not s.is_category]
        if kind in COLLAPSIBLE:
            assert len(precise) <= MAX_SOURCES_PER_KIND
        if any(s.kind is kind and s.is_category for s in a.sources):
            assert not precise


@given(labels, labels)
def test_trust_is_monotone(a: Label, b: Label) -> None:
    # Joining can never turn an untrusted value into a trusted one.
    if a.join(b).only_from(POLICY_TRUSTED):
        assert a.only_from(POLICY_TRUSTED)
        assert b.only_from(POLICY_TRUSTED)


@given(labels)
def test_a_label_with_a_category_is_never_trusted(a: Label) -> None:
    if any(source.is_category for source in a.sources):
        assert not a.all_sources(lambda _source: True)


@given(labels, st.lists(labels, max_size=4))
def test_derive_label_covers_all_inputs(base: Label, others: list[Label]) -> None:
    value = Labeled(0, base)
    inputs = [Labeled(i, label) for i, label in enumerate(others)]
    derived = value.derive(1, *inputs)
    assert derived.label.covers(base)
    for label in others:
        assert derived.label.covers(label)


@given(labels)
def test_json_round_trip(a: Label) -> None:
    # Through real JSON text, as the label store will do.
    assert MINT.load_label(json.loads(json.dumps(a.to_json()))) == a


@given(json_values)
def test_from_json_rejects_garbage_cleanly(data: object) -> None:
    # Fuzz: arbitrary JSON either loads or fails with one of our errors.
    with contextlib.suppress(LabelFormatError, InvalidSourceError, EmptyLabelError):
        Label.from_json(data)


@given(st.sampled_from(NON_OWNER_KINDS), st.text(max_size=40))
def test_no_name_can_become_owner_or_category(kind: SourceKind, name: str) -> None:
    try:
        source = MINT.source(kind, name)
    except InvalidSourceError:
        return
    assert source.kind is not SourceKind.OWNER
    assert not source.is_category


# Adversarial constructor input: anything that is not the right type must be
# rejected with one of our errors, never stored and never a crash elsewhere.
junk = st.one_of(
    st.text(max_size=12), st.integers(), st.none(), st.lists(st.text(max_size=4), max_size=3)
)


@given(junk, st.one_of(junk, st.sampled_from(NAMES)))
def test_wrongly_typed_sources_are_rejected(kind: object, name: object) -> None:
    with contextlib.suppress(InvalidKindError, InvalidSourceError):
        source = Source(kind, name)  # type: ignore[arg-type]
        assert isinstance(source.kind, SourceKind)


@given(st.one_of(junk, st.sets(sources, max_size=4)), junk)
def test_wrongly_typed_labels_are_rejected(raw_sources: object, raw_readers: object) -> None:
    errors = (EmptyLabelError, NotASourceError, InvalidReadersError)
    with contextlib.suppress(*errors):
        label = Label(raw_sources, raw_readers)  # type: ignore[arg-type]
        assert isinstance(label.sources, frozenset)
        assert isinstance(label.readers, Readers)


@given(st.sampled_from(sorted(TRUSTED_KINDS - {SourceKind.OWNER})), st.sampled_from(NAMES))
def test_trusted_sources_cannot_be_created_without_the_mint(kind: SourceKind, name: str) -> None:
    with contextlib.suppress(UnmintedSourceError):
        Source(kind, name)
        raise AssertionError(kind)
