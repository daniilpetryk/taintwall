# Code quality gates

Every rule here runs in CI on every pull request, inside the required checks
(`python 3.13`, `python 3.14`, `hooks`). Nothing depends on a reviewer
remembering it. To change a limit, edit the number in `pyproject.toml`; the
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
| Layering inside packages | Import Linter `layers` | `[tool.importlinter]` | see below |
| No import cycles between siblings | Import Linter `acyclic_siblings` | `[tool.importlinter]` | none |
| Security boundaries (LLM, tools, chain, mint) | Import Linter `forbidden`, `protected` | `[tool.importlinter]` | see design.md |
| Branch coverage | pytest-cov | `[tool.coverage.report] fail_under` | 95% |
| Types | mypy strict + Pyrefly | `[tool.mypy]`, `[tool.pyrefly]` | both clean |
| Unused or undeclared dependencies | deptry | `[tool.deptry]` | none |

Preview rules are opted in one by one (`explicit-preview-rules = true`), so a
ruff upgrade cannot switch on new preview rules silently. The ruff version is
pinned in `uv.lock`.

Tests are exempt from the method-count rules: a test class groups related
tests and may hold many. They still pass every other ruff rule and both type
checkers.

## Layers

A module may import only from layers below it. Names joined by `|` are
independent: they sit on one level and may not import each other.

```
taintwall.core
  interpreter
  plan | values | mint
  labels | errors

taintwall.core.interpreter
  runtime                         Interpreter: one requester, one set of tools
  evaluator                       one run's state; dispatches each syntax node
  reads | literals | operations   one handler per syntax node
  context                         the protocol handlers depend on
  tools                           tool registry, source stamp, failure isolation
  builtins                        len, abs, min, max, sum
  operators                       type rules, values and labels of operators
  walk                            reading nested values: plain, deep label, depth
  limits | trace                  run limits and guard; provenance trace
  arguments | location            call arguments; error locations

taintwall.core.labels
  value                           Labeled values, immutability, typed equality
  label                           Label, Readers, join, storage format
  source                          SourceKind, Source, the mint capability
  errors                          typed errors with bounded previews
```

Handlers depend on the `Context` protocol rather than on the evaluator
(dependency inversion). That is what lets the evaluator import them for its
dispatch table without a cycle.

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
  the private capability token) and `PLR6301` (holding a `Mint` instance is
  the capability, so its methods stay instance methods).
- `tests/**`: assertions, magic values, private helpers, test classes with
  many methods, and no docstrings (test names are the documentation).
