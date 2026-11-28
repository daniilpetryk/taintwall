# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""What builtins and methods take, as the checker can tell before a run.

Mirrors ``taintwall.core.interpreter.builtins`` and ``methods`` in the order
they check: the number of arguments first (``BAD_ARGUMENTS``), then what each
argument must be (``TYPE_MISMATCH`` for a builtin, ``BAD_ARGUMENTS`` for a
string method's arguments). A rule refuses only what no value with the known
type could pass, so a plan that might succeed is never refused here.
"""

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from typing import Final

from taintwall.core.errors import PlanErrorCode
from taintwall.tools.infer import Static, may_fit
from taintwall.tools.schema import ListOf, RecordOf, Scalar

type Kind = Callable[[Static], bool]
"""Whether a value with what is known might be what an argument must be."""

_ANY: Final = 2**31
"""Upper bound of an arity that is not limited."""


def _number(kind: Static) -> bool:
    return may_fit(kind, Scalar.FLOAT)  # an int or a float; never a bool


def _whole(kind: Static) -> bool:
    return may_fit(kind, Scalar.INT)


def _tuple(kind: Static) -> bool:
    # may_fit never reads a list type's item: any list may be empty.
    return may_fit(kind, ListOf(Scalar.ANY))  # pragma: no mutate - see above


def _atom(kind: Static) -> bool:
    return not isinstance(kind, ListOf | RecordOf)


def _text(kind: Static) -> bool:
    return may_fit(kind, Scalar.STR)


def _parsable(kind: Static) -> bool:
    # int and float take numbers and text that is a number (checked at run time).
    return _number(kind) or _text(kind)


def _anything(_kind: Static) -> bool:
    return True


@dataclasses.dataclass(frozen=True, slots=True)
class Rule:
    """How many arguments a callable takes, and what each must be."""

    arity: range
    kinds: tuple[Kind, ...] = ()
    """One per position; past the end, the last one applies."""
    mismatch: PlanErrorCode = PlanErrorCode.TYPE_MISMATCH
    """The code when an argument is of the wrong kind."""

    def check(self, arguments: Sequence[Static]) -> PlanErrorCode | None:
        """The code a run would raise for these arguments, if it surely would."""
        if len(arguments) not in self.arity:
            return PlanErrorCode.BAD_ARGUMENTS
        kinds = self.kinds or (_anything,)
        for position, argument in enumerate(arguments):
            if not kinds[min(position, len(kinds) - 1)](argument):
                return self.mismatch
        return None


BUILTINS: Final[Mapping[str, Rule]] = {
    "abs": Rule(range(1, 2), (_number,)),
    "round": Rule(range(1, 3), (_number, _whole)),
    "sum": Rule(range(1, 2), (_tuple,)),
    "any": Rule(range(1, 2), (_tuple,)),
    "all": Rule(range(1, 2), (_tuple,)),
    "min": Rule(range(1, _ANY)),
    "max": Rule(range(1, _ANY)),
    "len": Rule(range(1, 2)),
    "bool": Rule(range(1, 2)),
    "int": Rule(range(1, 2), (_parsable,)),
    "float": Rule(range(1, 2), (_parsable,)),
    "str": Rule(range(1, 2), (_atom,)),
    "range": Rule(range(1, 4), (_whole,)),
    "sorted": Rule(range(1, 2), (_tuple,)),
}
"""Every builtin of the plan language (``taintwall.core.interpreter.BUILTINS``)."""

_WORDS: Final = PlanErrorCode.BAD_ARGUMENTS
"""String methods refuse a non-string argument as a bad argument, not a type."""

METHODS: Final[Mapping[str, Rule]] = {
    "lower": Rule(range(1), mismatch=_WORDS),
    "upper": Rule(range(1), mismatch=_WORDS),
    "strip": Rule(range(2), (_text,), _WORDS),
    "split": Rule(range(2), (_text,), _WORDS),
    "startswith": Rule(range(1, 2), (_text,), _WORDS),
    "endswith": Rule(range(1, 2), (_text,), _WORDS),
    "replace": Rule(range(2, 3), (_text,), _WORDS),
    "join": Rule(range(1, 2), (_tuple,), _WORDS),
    "get": Rule(range(1, 3), (_text, _anything), _WORDS),
}
"""Every method of the plan language (``taintwall.core.syntax.METHODS``), by name."""
