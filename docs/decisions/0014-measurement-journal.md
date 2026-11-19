# 14. Measurements are a journal of patches; numbers stay in result files

- **Status:** accepted
- **Date:** 2026-11-19
- **Where:** [#27](https://github.com/daniilpetryk/taintwall/pull/27)

## Context

An engineer should see how the product evolved and why each number was measured; copied numbers drift from the data.

## Decision

`bench/results/journal.json` has one entry per patch (what changed, question, result, decision, cost). A measurement points at a committed result file and key; the site reads the value from there. A change is better or worse only when the 95% intervals do not overlap. CI fails if a result file is not in the journal or a plan census no longer matches its plans.

## Consequences

Every new benchmark run comes with a journal entry; the [measurements page](https://daniilpetryk.github.io/taintwall/measurements.html) is rebuilt on each release.
