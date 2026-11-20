# 17. Tools come from trusted specs; a hook sees every call before it runs

- **Status:** accepted
- **Date:** 2026-11-20
- **Where:** [#30](https://github.com/daniilpetryk/taintwall/pull/30)

## Context

The planner (stage 2, PR 4) needs each tool's signature and description, the replanning rule (Q3) needs to know whether a call changed something, and the policies of stage 3 need to see a call's arguments, their labels and the pc before it runs. A tool's description reaches the planner, and an MCP server's own description is text an attacker can control (tool poisoning).

## Decision

A tool is a `ToolSpec` from trusted configuration: name, description, typed parameters, result type, effects (`writes`, `moves_funds`, `outward`; required) and readers (Q9; private unless stated); its implementation is any Python callable, which receives only the arguments the plan gave and whose result must have the spec's type. Descriptions never come from a tool server. Types are the few the plan language can produce (atoms, optional, lists, records) and are read from JSON schemas, refusing anything else.

The interpreter takes a `CallHook` that runs before every tool call, after the arguments' labels are read, with the arguments, their deep labels, the pc and the line. The registry's `CallLog` is one: it checks arguments against the spec (`BAD_ARGUMENTS` with the tool's name, before any side effect) refuses a tool it has no spec for, and records each call with its effects and pc; `effects_happened` is what the replanning loop reads. `Hooks` chains several hooks in order (refusing ones first). Results come back labeled in every part with the tool's source and readers.

## Consequences

The planner's prompt, the static checker (PR 2) and the replanning loop read the same specs. Stage 3's policy engine becomes another hook. For AgentDojo, `bench/agentdojo/registry.py` builds specs from the suite's own function definitions; effects and readers per function are listed there, and unknown functions count as changing something.
