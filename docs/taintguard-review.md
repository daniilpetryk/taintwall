# taintguard review (before stage 1)

Reviewed 2026-11-18 from the PyPI source distribution `taintguard 0.1.0`
(published 2026-08-07, MIT, the only release, no linked repository; sha256 of
the sdist checked against PyPI). Read only: nothing was installed or run. Its
specification is written in Thai; the code comments too.

## What it is

A deterministic guard in front of an ordinary agent's tool calls: the LLM
reads untrusted data and proposes calls as usual, and `Guard` decides
`ALLOW`, `BLOCK` or `ESCALATE` before each call runs.

- **Provenance by text matching.** A per-session `TaintLedger` records spans
  of untrusted text (tool output, email, OCR). Before a call, each argument
  in `taint_fields` is normalised (lower case, punctuation removed, so
  `999-9` matches `9999`) and checked for containment in a span or a shared
  3-gram of tokens. Values shorter than 4 characters never match; `trust()`
  whitelists known-good values against false positives.
- **Declarative rules** on arguments: `Max`, `Min`, `In` (against context
  such as the user's own accounts), `Matches`, `Predicate`.
- **Risk classes** `READ` (no taint check), `WRITE`, `CRITICAL` (approval by
  default), per-session call limits, fail-closed for tools without a policy.
- **Structured block errors** returned to the LLM so it can correct itself,
  and an audit log (memory, JSONL or callback sinks). Adapters for the
  OpenAI SDK and a plain dispatcher. Depends only on pydantic.

Its own design notes state the key limit: taint bound to Python objects
cannot cross the LLM, because the model emits arguments as new strings, so
it matches text instead; and it cannot catch an argument the attacker never
typed, such as "send everything", where the amount comes from a trusted tool.

## How taintwall differs

| | taintguard | taintwall |
|---|---|---|
| Who reads untrusted data | the same LLM that chooses the calls | only the quarantined reader; the planner never sees it (CaMeL) |
| Provenance | heuristic: does the argument's text resemble an untrusted span | exact: labels propagate through every operation of the plan's interpreter |
| Evasion by transformation | paraphrase, encoding, splitting or arithmetic on a value defeats the match | values never pass through the planner as text; a derived value keeps its sources |
| Attacker-chosen action with trusted arguments ("send everything") | not caught by provenance; needs rules and approval | the injected text cannot reach the planner, so it cannot choose the action |
| False positives | coincidental overlap between trusted and untrusted text; needs `trust()` lists | none from coincidence: provenance is structural |
| Principals, readers, memory | no; one session, one user | several principals (C), readers (Q9), labels persisted in memory and stores (B) |
| Effects | argument rules only | simulation diff checked against the plan's arguments (A) |
| Cost | no extra model call | planner and reader calls, plan checking, typed retry (D) |

The two answer different questions. taintguard is a cheap drop-in for an
existing agent loop and protects against the common case (an injected
account number copied into a call). taintwall changes the architecture so
injected text cannot steer control flow at all, and tracks provenance
exactly, at the price of a planner/reader split and an interpreter.

## Worth taking from it

- **Per-session call limits** (`max_calls_per_session`) as a policy primitive
  next to amount limits: cheap, and they bound loops a planner might write.
- **Context-relative rules** (`In("to_account", ctx="own_accounts")`): our
  address-book and verified-list sources play this role; keep the policy
  syntax as direct.
- **Fail closed for tools without a policy**, with a warn mode for
  migration: already our default (Q-decisions); the migration mode is a
  usability idea for stage 3.
- **Audit sinks** (JSONL, callback): the provenance trace from stage 1 covers
  the data; a sink interface for decisions belongs to the policy engine.
- **Its honest limits section** is a good model for our README: state what
  the runtime does not catch.

## Consequences for the plan

No design decision changes. The comparison strengthens the case for the
planner/reader split and exact labels, and it gives the evaluation a
natural extra baseline: a text-matching guard on the same plain agent, to
show where heuristic provenance fails (transformed values, actions chosen
by the attacker) and where it is enough. Adding it is cheap (one adapter
around the plain agent's tool executor) and optional, for stage 3.
