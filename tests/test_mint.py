# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""The mint: each factory makes exactly one precise, trusted source.

Called inside the tests, not at import time, so mutation testing sees them.
A factory that returned a category (``name=None``) or the wrong kind would
silently change what a policy trusts.
"""

from collections.abc import Callable

import pytest

from taintwall.core.labels import TRUSTED_KINDS, Label, Source, SourceKind
from taintwall.core.mint import Mint

K = SourceKind


@pytest.mark.parametrize(
    ("make", "kind", "name"),
    [
        (lambda mint: mint.plan(), K.PLAN, "literal"),
        (lambda mint: mint.owner(), K.OWNER, "owner"),
        (lambda mint: mint.address_book("alice"), K.ADDRESS_BOOK, "alice"),
        (lambda mint: mint.verified("usdc"), K.VERIFIED_LIST, "usdc"),
        (lambda mint: mint.user_confirmed("call-7"), K.USER_CONFIRMED, "call-7"),
        (lambda mint: mint.source(K.ADDRESS_BOOK, "bob"), K.ADDRESS_BOOK, "bob"),
    ],
)
def test_factories_make_precise_trusted_sources(
    make: Callable[[Mint], Source], kind: SourceKind, name: str
) -> None:
    source = make(Mint())
    assert (source.kind, source.name) == (kind, name)
    assert not source.is_category
    assert source.kind in TRUSTED_KINDS
    assert Label.of(source).only_from({kind})


def test_load_label_keeps_trusted_sources() -> None:
    mint = Mint()
    label = Label.of(mint.address_book("alice"), Source.tool("rpc"))
    assert mint.load_label(label.to_json()) == label
