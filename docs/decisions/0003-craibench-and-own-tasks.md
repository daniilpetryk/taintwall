# 3. Run on CrAIBench and add own anvil tasks (Q13 outcome)

- **Status:** accepted
- **Date:** 2026-11-17
- **Where:** [`d5c9f94`](https://github.com/daniilpetryk/taintwall/commit/d5c9f94)

## Context

Q13 required reading CrAIBench before choosing a web3 benchmark: run on it if its tools are mocked, extend it if they are on-chain, never evaluate only on our own suite.

## Decision

CrAIBench's tools are mocked, so we run on it as is (an AgentDojo fork, MIT, 174 crypto user tasks, 47 injection tasks) and add 10–15 own anvil tasks only where it falls short. It lives in its own uv project because it installs as `agentdojo` too.

## Consequences

Two benchmark projects with separate environments. The memory-injection channel of CrAIBench became the main comparison for contribution B (ADR 0009).
