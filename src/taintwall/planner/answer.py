# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""``answer(text)``: how a plan tells the user what it did or found.

A tool like any other, so what the user is told carries the labels of what
it was built from (stage 3's policies decide whether a requester may see it).
Answering is an effect: once the user has been told something, the plan is
not run again from the start (Q3), or the user would hear it twice.
"""

from typing import Final

from taintwall.tools import Effects, Implementation, Param, Scalar, ToolSpec

NAME: Final = "answer"

SPEC: Final = ToolSpec(
    NAME,
    "Tell the user the result. Call it once, at the end.",
    (Param("text", Scalar.STR, description="What to tell the user"),),
    Scalar.NONE,
    Effects(writes=True),
)


class ReservedToolError(ValueError):
    """A tool given to the planner takes the name of the planner's own tool."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name!r} is the planner's own tool; rename this one")


def answer_tool() -> tuple[ToolSpec, Implementation, list[str]]:
    """The tool, its implementation, and the list it appends every answer to."""
    said: list[str] = []

    def answer(text: str) -> None:
        said.append(text)

    return SPEC, answer, said
