# Security policy

taintwall is a security project: a runtime that is supposed to stop prompt-injection attacks against AI agents that hold a crypto wallet. Reports that show a way around that defense are the most valuable contribution you can make.

## Status

The project is in the design phase. There are no releases yet; `main` (stable) and `dev` (integration) are the only supported branches. Until a release says otherwise, **do not use taintwall with real keys or real funds**. It is built and tested only against local anvil forks and testnets.

## Reporting a vulnerability

Report privately through GitHub: **[Report a vulnerability](https://github.com/daniilpetryk/taintwall/security/advisories/new)** (Security tab → Advisories). Do not open a public issue or pull request for a security problem.

Please include:

- what you found and which component it affects (planner, interpreter, labels, policy engine, simulation, signer, CI or supply chain);
- the steps or a minimal plan, prompt or payload that reproduces it;
- the impact: which asset or invariant is at risk (funds, an approval, a vote, private data).

This is a single-maintainer project, so expect best effort:

| Step | Target |
|---|---|
| Acknowledge the report | within 7 days |
| Initial assessment | within 14 days |
| Fix or mitigation for confirmed issues | as soon as practical, coordinated with you |

Credit is given in the advisory unless you ask otherwise. There is no bug bounty.

## Scope

In scope is anything that breaks the guarantees in the [threat model](docs/design.md#threat-model), for example:

- untrusted data (token or NFT metadata, DAO proposals, calldata, web pages, MCP responses, other chat participants) reaching the planner, or influencing a tool call without the policy seeing its provenance;
- a value losing its taint label: through an operation, control flow, the Q-LLM, the label store or memory;
- a transaction signed although the policy or the simulation should have refused it, or an effect missing from the simulation diff;
- the signer accepting a request without a valid decision token, or exceeding its own limits;
- supply-chain issues in this repository: workflows, pinned actions, images or dependencies.

Out of scope, as stated in the threat model: a compromised owner account or machine, leaked keys, malicious code running inside an MCP server, a lying RPC node, and attacks whose only result is misleading text shown to the user.

## Safe harbor

Good-faith research within this policy is welcome. Test only against your own local setup or a testnet; never against other people's wallets, funds or services.
