# 11. A failure carries the label of everything it depends on

- **Status:** accepted
- **Date:** 2026-11-19
- **Where:** [#24](https://github.com/daniilpetryk/taintwall/pull/24) (three review rounds)

## Context

A run-time failure is itself data: a division by an untrusted zero, a tool that fails on attacker input, or running out of steps after a secret-dependent loop each tell the planner something. Review found failures that looked clean in all three cases.

## Decision

Every run-time failure is a `LabeledPlanError`: the values its statement touched and, in strict mode, the pc; a failing tool adds its own source; a value too large adds its own label; a `STEP_LIMIT` adds every value and condition the run has seen, because the shared budget was spent by all of them. Argument labels are read before a tool runs, so a budget failure never follows a side effect.

## Consequences

The replanning loop (stage 2) decides from the label whether the planner may see a failure. Found by a noninterference property test that runs failing tools and small budgets.
