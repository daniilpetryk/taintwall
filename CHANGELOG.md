# Changelog

Every release to `main`, newest first; there is one release per completed stage. Each one is a git tag (`vX.Y.Z`) and a
GitHub Release whose notes are the section below: what the release is for,
what changed, and what it measured. Numbers live in the
[measurements](https://daniilpetryk.github.io/taintwall/measurements.html);
the reasons for decisions in the [decision log](docs/decisions/README.md).

Versions follow [Semantic Versioning](https://semver.org/). Before 1.0 the
minor version counts releases to `main` and nothing is a stable API yet.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Stage 2 (planner and reader) is in progress; it is released as 0.4.0 when complete.

### Added
- Study of what Python planners write: five models, the 16 AgentDojo banking
  tasks, with and without the plan language in the prompt. Stating the
  language takes Haiku 5.5 from 0% to 88% of plans accepted; it decides
  what the language adds next ([report](docs/planner-plans.md), #26).
- Measurements page on GitHub Pages: patch notes with each number's change
  since it was last measured, built from `bench/results/journal.json` (#27).
- This changelog, versioned releases, and the decision log (ADRs) (#28).
- Plan language version 2: string methods, `record.get`, number formats,
  `is None`, `a if c else b`, `+=` and nine builtins, each labeled by what
  its result depends on. Recounting the study's plans: with the rules in the
  prompt, DeepSeek V3.2 goes from 19% to 88% of plans accepted, GPT-5 mini
  from 62% to 94%, Haiku 5.5 to 100% (#29).

- Tool registry: tools from trusted specs (typed parameters, result
  type, effects, readers), results labeled in every part, and a hook that
  sees every tool call with its arguments' labels and the pc before it runs;
  the call log refuses ill-typed arguments before any side effect and
  tells the replanning loop whether anything changed. AgentDojo's banking
  functions run through it (#30).
- Static plan checker: before a plan runs, every statement that would fail
  whatever the data (unknown tools, names read before they are bound,
  arguments that do not fit a tool, builtin or method, missing fields,
  indexes, loops and f-string parts a value cannot take), as typed errors
  from the plan text only, and every call with its effects and constant
  arguments. Over the study's 91 accepted plans it stops 21, most for
  formatting a tool's record in an f-string (#36).
- The reader (Q-LLM) as the tool `query_reader`: a schema of field names and
  types in, checked fields out, `have_enough_info` on every reply; what it
  extracts keeps the data's labels, no data can close the fence around it,
  and each reader has a call limit. The LLM layer: structured output through
  OpenRouter as stage 0 found it must be called, replies checked against
  their schema (refused before any call if it cannot be checked), retries
  with backoff, typed errors that never carry a reply, an on-disk response
  cache and a ceiling on calls and cost (#38).
- The planner: a P-LLM prompt built from the plan language's rules and the
  tool registry, an `answer` tool, and a loop that checks and runs each
  program and retries within Q3's bounds with typed errors only (with those
  of up to three earlier attempts), never after an effect; a call a policy
  refuses ends the run. Tools and policies are built afresh for each
  request (#39).

### Changed
- `and`/`or` operands after the first, and the branch of `a if c else b`,
  run under a pc of what decided them (#29).
- The LLM layer: a failed call carries what it was billed (`LLMError.usage`),
  and the budget counts it; a provider's `Retry-After` is honored, up to 60
  seconds (#39).
- Tool registry: a call's arguments are checked against the tool before any
  policy sees it, so policies see only well-typed calls (#39).

## [0.3.0] - 2026-11-19

Stage 1 complete: the core that labels every value a plan computes.

### Added
- Implicit flows: `if`/`elif`/`else`, `for`, `while`, `break`, `continue`
  in plans, with a pc label in strict and lenient modes (decision Q2).
- Every run-time failure is a `LabeledPlanError` carrying the label of what
  it depends on (termination channel).
- Noninterference property tests over random plans with branches, loops,
  failing tools and small budgets (#24).

### Measured
- Nothing new: no planner yet, so no benchmark changes.

## [0.2.0] - 2026-11-18

Stage 0 baselines and the first half of stage 1.

### Added
- Provenance labels: sources, readers, principals, minted trust (#12).
- Plan interpreter for explicit flows, limits and typed errors (#13), split
  into small packages with clean-code gates in CI (#14).
- Benchmark runners for AgentDojo and CrAIBench with metered, cached model
  calls and committed results (#15, #18).

### Measured
- Undefended agent (Haiku 5.5): AgentDojo banking utility 69%, attack
  success 0% under both attacks; CrAIBench prompt injection 0–4%, memory
  injection 48–67% ([baselines](docs/baselines.md)).

### Security
- CrAIBench's vulnerable dependencies upgraded (#20, #21).

## [0.1.0] - 2026-11-17

Design and repository skeleton.

### Added
- Design, threat model and decisions Q1–Q13 ([design](docs/design.md)),
  architecture diagrams on GitHub Pages (#10).
- Python and Go skeleton, Docker images pinned by digest, lint, type,
  architecture, licensing and security tooling in CI (#1, #4, #5, #6).
- Security policy and Dependabot for every pin (#7).

[Unreleased]: https://github.com/daniilpetryk/taintwall/compare/v0.3.0...dev
[0.3.0]: https://github.com/daniilpetryk/taintwall/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/daniilpetryk/taintwall/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/daniilpetryk/taintwall/releases/tag/v0.1.0
