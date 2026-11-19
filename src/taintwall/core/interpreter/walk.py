# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Reading nested plan values: plain values, deep labels, depth.

Values nest as tuples and records of ``Labeled`` values. Every walk over that
structure draws from the run's read budget, because repetition shares
references and a small plan can build a value with very many paths.
"""

from collections.abc import Callable, Iterable, Iterator

from taintwall.core.errors import PlanErrorCode
from taintwall.core.interpreter.limits import Guard
from taintwall.core.labels import MAX_VALUE_DEPTH, Label, Labeled, join_all
from taintwall.core.values import Record


def read_label(own: Label, container: Label, *keys: Label) -> Label:
    """The label of reading a field or element: its own, the container's, the key's.

    One rule for ``r.f``, ``r[k]``, ``t[i]`` and ``r.get(k, d)``: what was read
    depends on what it is, where it was, and which key chose it.
    """
    return join_all(own, container, *keys)


def unwrap(labeled: Labeled[object], guard: Guard) -> tuple[object, Label]:
    """The value inside nested ``Labeled`` wrappers, with every wrapper's label.

    A tool may return ``Labeled(Labeled(False, a), b)``; deciding on the
    outer object would treat it as true and miss ``a``. Each wrapper read
    draws on the budget.
    """
    value, labels = labeled.value, [labeled.label]
    while isinstance(value, Labeled):
        guard.visit()
        labels.append(value.label)
        value = value.value
    return value, join_all(*labels)


def truth(labeled: Labeled[object], guard: Guard) -> tuple[bool, Label]:
    """Truthiness and what it depends on: emptiness only, so container labels."""
    value, label = unwrap(labeled, guard)
    return bool(value), label


def children(value: object) -> tuple[object, ...]:
    """Values directly inside a plan value."""
    if isinstance(value, Labeled):
        return (value.value,)
    if type(value) is tuple:
        return value
    if isinstance(value, Record):
        return tuple(field for _, field in value.fields)
    return ()


def plain(value: object, guard: Guard) -> object:
    """The value with every nested label removed, for computing and comparing."""
    guard.visit()
    # Iterative through wrappers: a tool can nest them far deeper than the
    # stack; containers recurse at most MAX_VALUE_DEPTH levels.
    while isinstance(value, Labeled):
        guard.visit()
        value = value.value
    if type(value) is tuple:
        return tuple(plain(item, guard) for item in value)
    if isinstance(value, Record):
        return {name: plain(field, guard) for name, field in value.fields}
    return value


def deep_label(labeled: Labeled[object], guard: Guard) -> Label:
    """The label of a value joined with the label of everything nested in it."""
    return join_all(labeled.label, *_nested_labels([labeled.value], guard.visit))


def labels_within(values: Iterable[Labeled[object]]) -> Iterator[Label]:
    """Every label in ``values``, nested ones included, each shared value once.

    Used only when a run has already failed, so it draws on no budget; it
    visits each distinct object once, which the run's sizes already bound.
    """
    return _nested_labels(values, lambda: None, once=True)  # pragma: no mutate - result unused


def _nested_labels(
    values: Iterable[object], visit: Callable[[], None], *, once: bool = False
) -> Iterator[Label]:
    """Labels of ``values`` and everything nested in them, depth first.

    ``visit`` is called per element read. ``once`` skips an object already
    read; budgeted walks read every path, which is what the budget counts.
    """
    seen: set[int] = set()
    pending = list(values)
    while pending:
        visit()
        item = pending.pop()
        if once:
            if id(item) in seen:
                continue
            seen.add(id(item))
        if isinstance(item, Labeled):
            yield item.label
        pending.extend(children(item))


def check_nesting(value: object, guard: Guard) -> None:
    """Reject a container nested deeper than ``MAX_VALUE_DEPTH``.

    ``Labeled`` checks depth only down to the next nested ``Labeled`` value,
    so ``a = (a,)`` repeated would grow without bound and later overflow the
    stack in comparisons. Only building tuples and records adds a level, so
    the evaluator calls this there. Shared elements are measured once.
    """
    depths: dict[int, int] = {}
    pending: list[tuple[object, bool]] = [(value, False)]
    while pending:
        item, ready = pending.pop()
        inner = children(item)
        if ready or not inner:
            depths[id(item)] = _depth(item, inner, depths, guard)
        elif id(item) not in depths:
            guard.visit()
            pending.append((item, True))
            pending.extend((child, False) for child in inner if id(child) not in depths)


def _depth(item: object, inner: tuple[object, ...], depths: dict[int, int], guard: Guard) -> int:
    level = int(type(item) is tuple or isinstance(item, Record))
    depth = level + max((depths[id(child)] for child in inner), default=0)
    if depth > MAX_VALUE_DEPTH:
        raise guard.fail(PlanErrorCode.VALUE_TOO_LARGE)
    return depth
