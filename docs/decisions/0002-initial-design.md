# 2. Initial design: CaMeL base, four contributions, decisions Q1–Q13

- **Status:** accepted
- **Date:** 2026-11-17
- **Where:** [`cb73c17`](https://github.com/daniilpetryk/taintwall/commit/cb73c17), [`629c7c9`](https://github.com/daniilpetryk/taintwall/commit/629c7c9)

## Context

A wallet agent reads attacker-controlled text (token names, proposals, messages, memory) and can move funds irreversibly. Prompting the model to be careful is not a defense.

## Decision

Build on CaMeL: a planner that never sees data writes a plan in a Python subset; a quarantined reader parses data into schemas; an interpreter labels every value with its sources and readers; policies check labels before each tool call. Add four contributions: effect-level policies on simulated transactions (A), labels that persist in memory and stores (B), several principals (C), plans that run on cheap models (D). Thirteen open questions were decided up front (Q1–Q13, see [`design.md`](../design.md#design-decisions)); Q2, Q3 and Q7 are switches benchmarked both ways.

## Consequences

Every later ADR refines one of these. The design is read in `design.md`; the diagrams are on the site.
