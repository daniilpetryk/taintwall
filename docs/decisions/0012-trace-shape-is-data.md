# 12. The trace's shape is data

- **Status:** accepted
- **Date:** 2026-11-19
- **Where:** [#24](https://github.com/daniilpetryk/taintwall/pull/24)

## Context

Trace events hold plan text, never data, so the trace looked safe to show the planner. With branches, which events exist depends on the conditions.

## Decision

The trace and the set of assigned variables are labeled by `RunResult.control` and never reach the planner unfiltered.

## Consequences

Stage 2's replanning shows the planner typed errors only, not traces.
