# 10. Strict implicit flows by default

- **Status:** accepted
- **Date:** 2026-11-19
- **Where:** [#24](https://github.com/daniilpetryk/taintwall/pull/24)

## Context

Decision Q2 made implicit flows a switch. A plan can leak a secret through which branch it takes: after `x = 0; if s: x = 1`, `x` reveals `s` even when the branch was not taken.

## Decision

Both modes track a pc label and record it on every trace event. Strict mode (the default) puts the pc into values created under a condition, relabels every variable a block may assign after it ends, puts the test of `if c: break` on the rest of the loop, and labels a later failure caused by a skipped assignment. Lenient mode keeps data dependencies only, like CaMeL's normal mode.

## Consequences

Strict costs utility (more values look tainted); both modes are benchmarked in stage 3 before the default is final.
