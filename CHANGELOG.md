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

## [0.4.0] - 2026-11-28

Stage 2 complete: the planner and the reader, measured without policies.

The planner writes programs without seeing data; the tool registry, the
static checker and the labeling interpreter run them; the reader extracts
fields from what they read. On AgentDojo banking the architecture costs 9
points of utility (62% against the plain agent's 71%, both over five draws)
and 15 and 14 under attack, and buys nothing measurable there: Haiku 5.5
already refuses these attacks, so ASR is 0% for both. What it buys is
measured next, on CrAIBench memory injection with policies (stage 3).

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
- AgentDojo banking through the planner, without policies: utility 56% with
  Q3's retries and with CaMeL's one shot alike (plain agent 69%); retries cut
  plan failure under attack from 28% to 22% on the same programs but change no
  score; ASR 0% as for the plain agent. The reader's all-or-nothing answer
  costs the most: 3 of 16 tasks ([report](docs/planner-banking.md), #40).
- Optional reader fields: a field the data does not state is left out of
  the reader's result instead of failing the call, and `have_enough_info` is
  about the question. On AgentDojo banking with retries, utility is back to
  the plain agent's 69% in that draw (62% over five, #45) and plan failure falls to 0% alone and 1% and 6%
  under attack; retries now change scores (#42, ADR 0021).
- A study of two hints in the planner's prompt (read reader fields with
  `info.get`, calculate in the program): the first removes `MISSING_FIELD`
  from first programs, but three prompts gave 11, 9 and 8 of 16 tasks, which
  is noise between draws of the planner on one program per task. Not
  adopted; planner-prompt changes wait for several draws per task (#44, ADR
  0022).
- Several draws per run (`--draws`), each with its own cache entries, and
  each rate's spread over them. Over five draws the planner with retries
  keeps 62% of banking tasks on average (50–69%; #42's 69% was the best
  draw), 58% and 57% under attack; retries rescue 40 and 37 attacked
  requests per five draws and lose none (#45).
- The baseline runner draws too (`baseline.py --draws`, `make bench-baseline
  DRAWS=5`) (#NN).

### Changed
- `and`/`or` operands after the first, and the branch of `a if c else b`,
  run under a pc of what decided them (#29).
- The LLM layer: a failed call carries what it was billed (`LLMError.usage`),
  and the budget counts it; a provider's `Retry-After` is honored, up to 60
  seconds (#39).
- Tool registry: a call's arguments are checked against the tool before any
  policy sees it, so policies see only well-typed calls (#39).

### Measured
- AgentDojo banking, Haiku 5.5, both models over five draws: the plain agent
  keeps 71% of tasks (69–75%; the stage 0 run was its lowest draw), 73% and
  71% under attack; the planner with Q3's retries 62% (50–69%), 58% and 57%;
  one shot 57%, 53% and 53%. Plan failure with retries 1% alone, 3% and 8%
  under attack. ASR 0% for both in every draw
  ([report](docs/planner-banking.md), #NN).
- What the planner writes: stating the plan language takes five models'
  accepted plans from 0–62% to 88–100% (#26, #29).

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

[Unreleased]: https://github.com/daniilpetryk/taintwall/compare/v0.4.0...dev
[0.4.0]: https://github.com/daniilpetryk/taintwall/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/daniilpetryk/taintwall/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/daniilpetryk/taintwall/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/daniilpetryk/taintwall/releases/tag/v0.1.0
