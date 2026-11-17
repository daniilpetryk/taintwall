# taintwall

A runtime for AI agents with a crypto wallet that defends against prompt injection by architecture, not by prompting.

The planner model never sees untrusted data. A quarantined model reads token names, invoices, proposals and chat messages, but has no tools. Every value carries provenance labels, every tool call goes through a policy engine, and every transaction is simulated on a local fork before an isolated signer will sign it.

The base design follows CaMeL (Google DeepMind and ETH, "Defeating Prompt Injections by Design"). This project adds:

- **A. Effect-level policies:** every balance or allowance change in a simulated transaction must be explained by a plan argument whose label passes.
- **B. Persistent labels:** memory, the address book and caches keep labels across sessions, which blocks memory-injection attacks.
- **C. Several principals:** in a group chat, only the owner's requests can move funds.
- **D. Plans that run on cheap models:** a static plan checker plus a bounded retry with typed errors only.

## Status

Design phase. No code yet.

- [`docs/design.md`](docs/design.md): design, threat model, decisions Q1–Q13, benchmark and MVP plan.
- [`docs/architecture.html`](docs/architecture.html): diagrams (open in a browser).

## Safety

Local anvil fork and testnets only. No real keys or funds.
