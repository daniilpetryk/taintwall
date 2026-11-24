# 18. The static checker refuses only what would fail whatever the data

- **Status:** accepted
- **Date:** 2026-11-24
- **Where:** [#31](https://github.com/daniilpetryk/taintwall/pull/31)

## Context

Cheap planners write plans that fail at run time (contribution D). A failure found before a plan runs can go back to the planner safely: nothing has been read or changed, and the error comes from the plan text and the tool specs alone (Q3). A failure found while running can depend on untrusted data, so it is the bounded, logged second loop. The plan-level approval (Q10) also needs the plan's possible effects before anything runs.

## Decision

`taintwall.tools.check_plan(plan, specs)` (also `Registry.check`) reads a parsed plan against the tool specs and reports, as typed `PlanError`s with the codes and details a run would give, every statement that cannot succeed: an unknown tool or name, a call whose arguments cannot bind or whose value cannot have the parameter's type, a field a closed record type lacks, a field read on a value that is never a record, a loop over a value that is never a tuple, a tool or builtin name used as a variable, `x += 1` before any `x = ...`. All errors are reported at once, in line order, at most 20.

Types are inferred flow-insensitively: each variable has the join of everything assigned to it, constants keep their value, and anything unclear is `object`. A check fails only when no value with what is known could pass (`may_fit`), so the checker never refuses a plan that would run; a property test on generated plans checks exactly that. A call in a branch that never runs is still reported: it is wrong code.

The checker also lists every tool call site with the tool's effects, and their union.

## Consequences

The checker is the first loop of the typed retry (PR 4) and the source of the plan-level approval summary. Over the 91 plans the language accepts from the planner study, it finds 3 sure failures (`float` as a value, a reader schema written as a list, a loop over a string): the language already stops most mistakes, so the value of the checker is cheap, safe feedback, not volume. Flow-insensitive inference misses a mistake hidden behind a reassignment; a flow-sensitive checker can come later if retries show the need.
