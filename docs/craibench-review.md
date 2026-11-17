# CrAIBench review (stage 0)

Reviewed 2026-11-17 from the public repo [`AthaSSiN/CrAIBench`](https://github.com/AthaSSiN/CrAIBench) (default branch `main`, last push 2026-01-09). Read only: nothing from the repo was installed or run.

## What it is

- **A fork of AgentDojo**, not a separate framework. The package is still called `agentdojo` (version 0.1.15; upstream AgentDojo is at 0.1.35), with four crypto suites added next to the original banking, slack, travel and workspace suites.
- **License:** MIT. Compatible with our Apache-2.0; attribution required if we vendor or copy code.
- **Python:** `requires-python >= 3.10`, the repo pins 3.12.2. Not yet tried on 3.14.
- Also ships a fine-tuning dataset and prior run logs (gpt-4o and gpt-4o-mini with spotlighting defenses), useful as reference numbers.

## Suites

| Suite | User tasks | Injection tasks | User × injection pairs |
|---|---:|---:|---:|
| `chain` | 32 | 14 | 448 |
| `chain2` | 8 | 11 | 88 |
| `crypto_trading` | 94 | 5 | 470 |
| `dao_nft` | 40 | 17 | 680 |
| **crypto total** | **174** | **47** | **1,686** |

Each pair can run with several attack types, so the full security matrix is far beyond the budget. We sample (Q13) and report confidence intervals.

## Are the tools on-chain?

**No, they are mocked.** The crypto clients (`chain_client.py`, `crypto_trading_client.py`, `dao_nft_client.py`) are in-memory Pydantic models: a `Wallet` with a list of `Transaction` records and token balances. Amounts are `float`, there are no decimals, no contracts, and no web3, RPC or anvil calls anywhere in the tool code.

## Consequences

1. **Q13 outcome: run on it, plus our own anvil tasks.** CrAIBench gives the headline web3 utility/ASR numbers. Contribution A (effect accounting on a simulated diff) cannot be exercised on mocked wallets, so we add 10–15 own tasks on an anvil fork as planned.
2. **One integration, two benchmarks.** Because CrAIBench is AgentDojo, the taintwall pipeline element we write for AgentDojo works on CrAIBench with at most small API differences between 0.1.15 and 0.1.35.
3. **Package name clash.** Upstream AgentDojo and CrAIBench both install as `agentdojo`, so they cannot share one environment. Plan: two separate uv projects, `bench/agentdojo` (upstream, current version) and `bench/craibench` (the fork, pinned to a commit), each with its own lockfile.
4. **Types are weaker than ours.** Float amounts and plain-string addresses mean our typed amounts and address labels map onto CrAIBench through an adapter, not natively.
5. **Prior baselines exist.** Their logged runs let us sanity-check our plain-agent baseline against published numbers before spending budget.
