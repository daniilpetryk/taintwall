# 8. Benchmarks in their own uv projects; results committed

- **Status:** accepted
- **Date:** 2026-11-18
- **Where:** [#15](https://github.com/daniilpetryk/taintwall/pull/15), [#18](https://github.com/daniilpetryk/taintwall/pull/18)

## Context

Benchmarks pin dependencies the core must not have, two of them install as `agentdojo`, model calls cost money, and some providers ignore `temperature`, so reruns drift.

## Decision

Each benchmark is a uv project under `bench/` with its virtualenv outside the repository. Every model call goes through a metered, disk-cached client with a cost ceiling. Summaries with 95% Wilson intervals and per-pair scores are committed to `bench/results/`; transcripts and caches stay local.

## Consequences

Baselines are computed once and reused. A rerun from the cache is free and reproduces the committed numbers.
