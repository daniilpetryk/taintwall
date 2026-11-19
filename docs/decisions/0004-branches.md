# 4. Branches: work on dev, release to main

- **Status:** accepted
- **Date:** 2026-11-17
- **Where:** [`5a05129`](https://github.com/daniilpetryk/taintwall/commit/5a05129)

## Context

CI, Pages and signed history need a stable branch, while daily work needs fast merges.

## Decision

`dev` is the default branch: feature PRs are squash-merged into it. A release merges `dev` into `main` with a merge commit; `main` requires signed commits and green checks, and GitHub Pages publishes from it. Versions are tagged on `main` (ADR 0015).

## Consequences

The site and releases lag `dev` until the next release; history on `main` is one merge per release.
