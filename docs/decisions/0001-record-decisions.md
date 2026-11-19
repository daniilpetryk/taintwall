# 1. Record decisions as dated ADRs

- **Status:** accepted
- **Date:** 2026-11-19
- **Where:** [#28](https://github.com/daniilpetryk/taintwall/pull/28)

## Context

`docs/design.md` states the design as it is now. An engineer joining later also needs to know when a rule appeared, what prompted it and what it replaced; that history was only in pull requests.

## Decision

Every decision that changes a rule, an interface or how we measure gets one file here: status, date, the PRs, context, decision, consequences. `design.md` stays the current summary and links here. A superseded decision keeps its file with its status changed.

## Consequences

Small overhead per decision. The first ADRs are written after the fact from the PR history (marked by their dates).
