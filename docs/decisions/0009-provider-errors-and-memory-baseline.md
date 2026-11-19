# 9. Provider errors are failures, not attack successes; memory injection is the headline baseline

- **Status:** accepted
- **Date:** 2026-11-18
- **Where:** [#18](https://github.com/daniilpetryk/taintwall/pull/18)

## Context

AgentDojo and CrAIBench score a pair whose model call failed as if the attack succeeded or the task failed, which inflates attack success. On the baseline model, prompt injection through tool output barely works (0–4%), while CrAIBench's memory injection succeeds in 48–67% of pairs.

## Decision

A provider error stops the run (`ProviderError`) instead of being scored. Memory injection is the main comparison for contribution B; prompt injection stays measured, where any utility cost is the price of the defense.

## Consequences

See [`baselines.md`](../baselines.md). The defense has to show memory-injection success near zero without losing more utility than the plain agent already loses.
