# 7. Clean-code rules are enforced in CI, not by review

- **Status:** accepted
- **Date:** 2026-11-18
- **Where:** [#14](https://github.com/daniilpetryk/taintwall/pull/14)

## Context

The first interpreter was one large module. Readability and architecture had been review comments, which drift.

## Decision

Limits on module, function and class size, banned vague module names, import-layer contracts, coverage of changed lines (100%), duplicate detection and a mutation-score floor (98%) run in CI. Every `pragma: no mutate` needs a reason. The PR template keeps only what a machine cannot check.

## Consequences

PRs are smaller and slower to write; reviews talk about design instead of style. See [`code-quality.md`](../code-quality.md).
