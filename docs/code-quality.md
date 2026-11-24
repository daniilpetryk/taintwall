# Code quality gates

Every rule here runs in CI on every pull request, inside the required checks
(`python 3.13`, `python 3.14`, `hooks`), except mutation testing, which runs
nightly. Nothing depends on a reviewer remembering it; what only a reader can
judge is the checklist in `.github/pull_request_template.md`. To change a limit, edit the number in `pyproject.toml`; the
pull request then shows the policy change as a diff.

## Where each rule lives

| What | Tool | Configured in | Limit |
|---|---|---|---|
| Every stable lint rule | ruff `select = ["ALL"]` | `[tool.ruff.lint]` | exceptions listed with a reason |
| Cyclomatic complexity per function | ruff `C901` | `[tool.ruff.lint.mccabe]` | 8 |
| Arguments per function | ruff `PLR0913`, `PLR0917` | `[tool.ruff.lint.pylint]` | 5 (positional 5) |
| Branches, returns, statements per function | ruff `PLR0912`, `PLR0911`, `PLR0915` | `[tool.ruff.lint.pylint]` | 10, 5, 40 |
| Local variables per function | ruff `PLR0914` (preview) | `[tool.ruff.lint.pylint]` | 12 |
| Boolean terms in one condition | ruff `PLR0916` (preview) | `[tool.ruff.lint.pylint]` | 4 |
| Nested blocks | ruff `PLR1702` (preview) | `[tool.ruff.lint.pylint]` | 3 |
| Public methods per class | ruff `PLR0904` (preview) | `[tool.ruff.lint.pylint]` | 12 |
| Method that ignores `self` | ruff `PLR6301` (preview) | `extend-select` | make it a function |
| Private name imported from another module | ruff `PLC2701` (preview) | `extend-select` | only the mint, documented |
| Lines per module | `tests/test_code_quality.py` | `[tool.taintwall.quality]` | 300 |
| Lines per function, docstring included | `tests/test_code_quality.py` | `[tool.taintwall.quality]` | 40 |
| Methods per class, private included | `tests/test_code_quality.py` | `[tool.taintwall.quality]` | 15 |
| Module names that say nothing (`utils`, `helpers`, `common`, `misc`, `manager`, `base`) | `tests/test_code_quality.py` | `[tool.taintwall.quality]` | banned |
| Lines per test module and test function | `tests/test_code_quality.py` | `[tool.taintwall.quality]` | 700, 40 |
| The same size, function, class and name limits for `bench/` | `tests/test_code_quality.py` | `[tool.taintwall.quality]` | as above |
| Benchmark runners: strict mypy and offline tests, per project | CI job `bench` | `bench/*/pyproject.toml` | clean |
| `# pragma: no mutate` without a written reason | `tests/test_code_quality.py` | | banned |
| Packages used only through their `__init__` from outside | `tests/test_code_quality.py` | `allowed_internal_imports` | exceptions listed with a reason |
| Duplicate code (6+ similar lines) | pylint `duplicate-code` only | `[tool.pylint]` | none |
| Layering inside packages | Import Linter `layers` | `[tool.importlinter]` | see below |
| No import cycles between siblings | Import Linter `acyclic_siblings` | `[tool.importlinter]` | none |
| Security boundaries (LLM, tools, chain, mint) | Import Linter `forbidden`, `protected` | `[tool.importlinter]` | see design.md |
| Branch coverage, whole project | pytest-cov | `[tool.coverage.report] fail_under` | 95% |
| Coverage of lines a pull request adds or changes | diff-cover | `.github/workflows/ci.yml` | 100% |
| Mutation score of `taintwall.core` and `taintwall.tools` (nightly) | mutmut + `scripts/mutation_score.py` | `min_mutation_score` | 98% (measured 100%) |
| Property tests (noninterference, trace coverage, label laws) | Hypothesis | `tests/conftest.py` profiles | 500 examples per PR, 5000 nightly |
| Types | mypy strict + Pyrefly | `[tool.mypy]`, `[tool.pyrefly]` | both clean |
| Unused or undeclared dependencies | deptry | `[tool.deptry]` | none |

Preview rules are opted in one by one (`explicit-preview-rules = true`), so a
ruff upgrade cannot switch on new preview rules silently. The ruff version is
pinned in `uv.lock`.

Tests are exempt from the method-count rules: a test class groups related
tests and may hold many. They have their own size limits and pass every other
ruff rule and both type checkers.

Local equivalents: `make check` (everything per pull request), `make diff-cov`
(changed lines against `origin/dev`), `make mutate` (mutation testing, slow).

## Layers

A module may import only from layers below it. Names joined by `|` are
independent: they sit on one level and may not import each other.

```
taintwall.core
  interpreter
  plan | values | mint            plan parsing; records; minting trusted sources
  syntax | labels                 which syntax the language allows | provenance labels
  errors                          typed plan errors

taintwall.core.interpreter
  runtime                         Interpreter: one requester, one set of tools
  evaluator                       one run's state; dispatches each syntax node
  reads | literals | operations   one handler per expression node
    | statements                  one handler per statement (if, for, while, ...)
  context                         the protocols handlers depend on (Context, Scope, ToolCalls)
  tools                           tool registry, source stamp, failure isolation
  builtins | methods              builtin functions | string methods and record.get
  operators                       type rules, values and labels of operators
  walk                            reading nested values: plain, deep label, depth, unwrap,
                                  truth, the label of a field or element read
  limits | trace | control        run limits and guard; provenance trace; pc and implicit flows
  arguments | location            call arguments; error locations

taintwall.tools
  reader                          query_reader: the Q-LLM as a tool, a fixed schema out
  registry                        specs with implementations; interpreter tools; a call log per run
  check | calls                   static checker: sure failures, calls, effects | the hook: argument checks and call records
  signatures | scope              what builtins and methods take | which reads come before any binding
  infer                           what a plan's values can be, without running it
  spec                            parameters, result, effects, readers, signature
  values | schema                 plan values to Python and back | parameter and result types

taintwall.llm
  openrouter | cache              the OpenRouter backend | replies cached by request hash
  client                          LLMClient, the structured request and reply, typed errors

taintwall.core.labels
  value                           Labeled values, typed equality
  label | immutability            Label, Readers, join, storage | which values may carry a label
  source                          SourceKind, Source, the mint capability
  errors                          typed errors with bounded previews
```

Handlers depend on the `Context`, `Scope` and `ToolCalls` protocols rather than on
the evaluator or the toolbox (dependency inversion). That is what lets the
evaluator import them for its dispatch table without a cycle. In the same
way `immutability` takes the already-checked type as a parameter instead of
importing `Labeled`.

## Contract tests

Mutation testing showed that checking error codes alone is not enough, so two
test modules pin the contracts other stages depend on:

- `tests/test_error_contract.py`: exact code, line and detail of every typed
  error (the planner retries from these alone, Q3), and every limit tested one
  value inside and one outside.
- `tests/test_trace_contract.py`: one exact trace event per syntax node (kind,
  line, detail, inputs), which the viewer and later policies read.

Every surviving mutant is either killed by a test or marked
`# pragma: no mutate` with a comment proving it equivalent (a change no
behavior can observe, such as `zip(strict=True)` where the lengths always
match). Where a mutant showed dead or roundabout code, the code was
simplified instead of tested: limit errors report the statement line held by
`Guard`, so no syntax node is threaded through the helpers; `Record` has
`len`, so truthiness is plain `bool`. Raise the floor when the score rises.

## One import path per name

`taintwall.core` re-exports nothing. Import each name from the package that
owns it, through that package's `__init__`: `taintwall.core.labels`,
`taintwall.core.interpreter`, and the modules `plan`, `values`, `errors` and
`mint`. The encapsulation check keeps outside code out of package internals.

## Design principles behind the limits

- **One responsibility per module.** A module's docstring says what it holds;
  if that needs "and also", split it.
- **Rules next to their data.** Label rules for operators live in
  `operators`, for calls in `tools` and `builtins`, for reads in `reads`. The
  evaluator holds state and dispatches, nothing else.
- **Depend on protocols at boundaries.** Node handlers see `Context`; tools are
  plain callables (`Tool`).
- **Tables over ladders.** Dispatch on syntax nodes, operators and value kinds
  goes through tables (`_HANDLERS`, `_ARITHMETIC`, `_CONTAINER_RULES`), so a
  new case is a new row and the complexity limits keep holding.
- **No inheritance for code reuse.** Composition and functions; classes only
  where there is state (`Evaluator`, `Guard`, `Trace`, `Toolbox`) or a
  capability (`Mint`).

## Exceptions

Every exception is a line in `pyproject.toml` with its reason:

- `src/taintwall/core/mint.py`: `PLC2701` (it is the one place allowed to use
  the private capability token), `PLR6301` (holding a `Mint` instance is the
  capability, so its methods stay instance methods), and two internal imports
  of `taintwall.core.labels` modules, which hold the token.
- Mutation runs skip `tests/test_code_quality.py` and `tests/test_architecture.py`:
  they read source files and would see mutmut's instrumented copies.
- `tests/**`: assertions, magic values, private helpers, test classes with
  many methods, and no docstrings (test names are the documentation).
