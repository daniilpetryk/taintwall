# 16. The plan language has versions; a plan census records its version

- **Status:** accepted
- **Date:** 2026-11-19
- **Where:** [#29](https://github.com/daniilpetryk/taintwall/pull/29)

## Context

Stage 2, PR 1a added the constructs the planner study asked for (ADR 0013). Recounting the study's plans with the larger language changed the stored census, and CI recounts every census, so the study's own numbers would have been rewritten: the measurements page would no longer show what the language accepted at the time. The same PR made `and`, `or` and `a if c else b` decide which code runs, which is control flow inside an expression.

## Decision

`taintwall.core.syntax.LANGUAGE_VERSION` names the set of plans the language accepts and is bumped whenever that set changes (version 2 is this PR). `make bench-census` writes `census-v<version>.json`; a census of an earlier version is kept as a record of its time, and CI checks only the parts of it that do not depend on the language. A run must also have a census of the current version, so a language change comes with a recount and a journal entry.

The later operands of `and`/`or` and the chosen branch of `a if c else b` run under a pc of what decided them, in both modes, so a tool call in `ok and send(...)` is recorded as depending on `ok`; the result carries the deciding labels as before.

## Consequences

The measurements page shows the study's acceptance under version 1 and the recount under version 2 as two measurements of the same metric, with the change between them. Bumping the version is a manual step; forgetting it fails CI only once the census no longer matches.
