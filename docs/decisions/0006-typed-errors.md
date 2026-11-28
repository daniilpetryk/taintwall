# 6. Errors to the planner are typed codes; tool text is dropped

- **Status:** accepted
- **Date:** 2026-11-18
- **Where:** [#13](https://github.com/daniilpetryk/taintwall/pull/13)

## Context

Decision Q3 sends the planner typed feedback, never text. An exception message from a tool, or a record key computed from data, can carry an injection straight into the planner.

## Decision

A failure is a `PlanErrorCode`, a plan line and at most a detail taken from the plan text. Tool exceptions (including `SystemExit`) become `TOOL_FAILED` with the message removed from the exception chain too.

## Consequences

The planner learns less from errors; ADR 0011 later adds that a failure's label decides whether the planner may see it at all.
