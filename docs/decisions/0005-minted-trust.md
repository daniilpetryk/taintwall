# 5. Trusted provenance only through a mint capability

- **Status:** accepted
- **Date:** 2026-11-18
- **Where:** [#12](https://github.com/daniilpetryk/taintwall/pull/12)

## Context

If any code can build a label with source `owner` or `plan`, a bug in integration code can launder attacker data into trusted data.

## Decision

Precise trusted sources (`plan`, `owner`, `address_book`, `verified_list`, `user_confirmed`) can only be created through `taintwall.core.mint.Mint`, including when loading stored labels. An import contract and an architecture test enforce who may import it. Untrusted code can still say where data came from (`tool`, `principal`).

## Consequences

Not a sandbox (runtime code is trusted by the threat model), but laundering by accident fails at review time and in CI.
