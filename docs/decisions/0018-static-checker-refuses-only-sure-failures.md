# 18. The static checker reports only statements that fail whatever the data

- **Status:** accepted
- **Date:** 2026-11-24
- **Where:** [#36](https://github.com/daniilpetryk/taintwall/pull/36)

## Context

Cheap planners write plans that fail at run time (contribution D). A failure found before a plan runs can go back to the planner safely: nothing has been read or changed, and the error comes from the plan text and the tool specs alone (Q3). A failure found while running can depend on untrusted data, so it is the bounded, logged second loop. The plan-level approval (Q10) also needs the plan's possible effects before anything runs.

## Decision

`taintwall.tools.check_plan(plan, specs)` (also `Registry.check`) reads a parsed plan against the tool specs and reports, as typed `PlanError`s with the codes and details a run would give, every statement that cannot succeed: an unknown tool, a name read before anything binds it (in plan order; a binding later in a loop counts only for a read that is conditional within that loop, and never for a `while` test), a call whose arguments cannot bind or whose value cannot have the parameter's type (tools, and builtins and methods by the interpreter's own rules), a field a closed record type lacks, a field, index, method or loop a value cannot take, a record or tuple in an f-string, a tool or builtin name used as a variable. All errors are reported at once, in line order, at most 20; a part that fails makes its expression unknown, so one mistake is reported once.

Types are inferred flow-insensitively: each variable has the join of everything assigned to it, constants keep their value, and anything unclear is `object`. A check fails only when no value with what is known could pass (`may_fit`). The guarantee is that every reported statement fails whenever it runs. A statement in a branch that does not run is still reported (it is wrong code), so a plan can run and still be refused for such a branch; a straight-line plan that runs is never refused, and on one that fails the run stops at the first reported statement or before it (property tests on generated plans). When a statement has two sure failures, the run reports the one it reaches first, which may be the other.

The checker also lists every tool call site with the tool's effects and the arguments written as constants, and the union of the effects.

## Consequences

The checker is the first loop of the typed retry (PR 4) and the source of the plan-level approval summary. Over the 91 plans the language accepts from the planner study, it stops 21: 16 format a record or tuple in an f-string (most often the dict `send_money` returns), 3 call `str()` on a list of transactions to hand it to the reader, one loops over a string and one passes a reader schema as a list (against the reader as the study prompt states it). The planner prompt should say that only atoms can be formatted or converted with `str()`. The constant arguments are what the plan-level approval (Q10) can show before anything runs. Flow-insensitive inference misses a mistake hidden behind a reassignment; a flow-sensitive checker can come later if retries show the need.
