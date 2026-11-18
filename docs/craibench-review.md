# CrAIBench review (stage 0)

Reviewed 2026-11-17 from the public repo [`AthaSSiN/CrAIBench`](https://github.com/AthaSSiN/CrAIBench) (default branch `main`, last push 2026-01-09). Read only at first; since run as `bench/craibench` (see [`baselines.md`](baselines.md)).

## What it is

- **A fork of AgentDojo**, not a separate framework. The package is still called `agentdojo` (version 0.1.15; upstream AgentDojo is at 0.1.35), with four crypto suites added next to the original banking, slack, travel and workspace suites.
- **License:** MIT. Compatible with our Apache-2.0; attribution required if we vendor or copy code.
- **Python:** `requires-python >= 3.10`, the repo pins 3.12.2. Not yet tried on 3.14.
- Also ships a fine-tuning dataset and prior run logs (gpt-4o and gpt-4o-mini with spotlighting defenses), useful as reference numbers.

## Suites

| Suite | User tasks | Injection tasks | Valid pairs |
|---|---:|---:|---:|
| `chain` | 32 | 14 | 64 |
| `chain2` | 8 | 11 | not registered in `load_suites` |
| `crypto_trading` | 94 | 5 | 470 |
| `dao_nft` | 40 | 17 | 155 |
| **runnable total** | **166** | **36** | **689** |

**Correction (2026-11-18, from running it):** not every user × injection
combination is valid. CrAIBench's run scripts (`run_script_chain.sh`,
`run_script_dao.sh`) list which injection tasks belong to each user task;
only `crypto_trading` allows every pair. The first version of this table
counted the full cross product (1,686). The valid pairs are kept in
`bench/craibench/pairs.json`. `chain2` exists in the source tree but is not
registered as a suite.

Each pair can run through two channels (prompt injection in tool output, and CrAIBench's memory injection) and several attack types. We run the small suites in full, sample `crypto_trading` (Q13), and report 95% Wilson intervals.

## Are the tools on-chain?

**No, they are mocked.** The crypto clients (`chain_client.py`, `crypto_trading_client.py`, `dao_nft_client.py`) are in-memory Pydantic models: a `Wallet` with a list of `Transaction` records and token balances. Amounts are `float`, there are no decimals, no contracts, and no web3, RPC or anvil calls anywhere in the tool code.

## Consequences

1. **Q13 outcome: run on it, plus our own anvil tasks.** CrAIBench gives the headline web3 utility/ASR numbers. Contribution A (effect accounting on a simulated diff) cannot be exercised on mocked wallets, so we add 10–15 own tasks on an anvil fork as planned.
2. **One integration, two benchmarks.** Because CrAIBench is AgentDojo, the taintwall pipeline element we write for AgentDojo works on CrAIBench with at most small API differences between 0.1.15 and 0.1.35.
3. **Package name clash.** Upstream AgentDojo and CrAIBench both install as `agentdojo`, so they cannot share one environment. Plan: two separate uv projects, `bench/agentdojo` (upstream, current version) and `bench/craibench` (the fork, pinned to a commit), each with its own lockfile.
4. **Types are weaker than ours.** Float amounts and plain-string addresses mean our typed amounts and address labels map onto CrAIBench through an adapter, not natively.
5. **Prior baselines exist.** Their logged runs let us sanity-check our plain-agent baseline against published numbers before spending budget.

## Running it (2026-11-18)

- Installs only with its own `requirements.lock`: the fork imports provider
  SDK names that later releases removed (`cohere`, `openapi-pydantic`), and
  its pinned `tokenizers` has no Python 3.13 wheel. `bench/craibench` uses
  Python 3.12 and turns that lock file into uv `constraint-dependencies`.
- Its OpenAI SDK (1.31) returns usage details as untyped extras, which broke
  our cost metering until it read usage as plain data (`bench/common`).
- Its resume check never finds finished results: logs are written under
  `<model>/<system message>/<suite>/…` (memory runs with a
  `_memory_injection` suffix) but looked up under `<model>/<suite>/…`. Every
  rerun repeats every pair; our runner reads results itself.
- Its lock file pins 17 packages with known vulnerabilities (106 Dependabot
  alerts: aiohttp, urllib3, h11, anyio, certifi, langchain-core, deepdiff,
  protobuf, fsspec). They are released from the constraints and resolve to
  patched versions; langchain 1.x no longer pulls aiohttp at all, and
  pydantic moves 2.7.3 → 2.9.2. The fork's offline tests and a live task
  pass on them. The SDKs that broke it (openai, cohere, openapi-pydantic)
  stay at its versions. deepdiff, the one released package that scores
  tasks, is used only by the workspace suite, so the chain, dao_nft and
  crypto_trading scores are unaffected; the baseline in `baselines.md` was
  computed on the old lock.
- Like AgentDojo, it scores a context-length or server error as
  `security=True`; our client turns provider errors into an error neither
  catches.
