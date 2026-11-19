# 13. The plan language grows by measured need

- **Status:** accepted
- **Date:** 2026-11-19
- **Where:** [#26](https://github.com/daniilpetryk/taintwall/pull/26)

## Context

Planners write ordinary Python (methods, imports, comprehensions). Measuring plan failure rates against a language they do not know would measure our language, not the models.

## Decision

Before writing the planner, five models planned the 16 banking tasks with and without the language in the prompt. Stating the rules is the main lever (Haiku 5.5: 0% to 88% accepted). Only constructs models keep writing despite the rules, and that are safe to label, are added: string methods, `record.get`, number formats, `is None`, conditional expressions, `+=`, a few builtins. Imports, definitions, mutation and comprehensions stay out.

## Consequences

See [`planner-plans.md`](../planner-plans.md). Replies need per-model output budgets: hidden reasoning cut 15 of 160 replies at 2000 tokens.
