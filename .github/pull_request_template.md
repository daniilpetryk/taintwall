## What and why

<!-- One paragraph: what changes, and which design decision or issue it serves. -->

## Checks a machine cannot do

CI already enforces lint, types, layers, sizes, complexity, coverage of changed
lines and duplicates (see `docs/code-quality.md`). Tick what only a reader can judge:

- [ ] Each new or changed module still has one responsibility, and its docstring says which.
- [ ] Names say what things are; no new vague names.
- [ ] Abstractions are needed (no protocol, class or module only to satisfy a limit).
- [ ] Anything that implements a protocol keeps its contract, not just its signature.
- [ ] Comments and docstrings are true after this change.
- [ ] Tests check the property that matters (labels, typed errors), not only that code runs.
- [ ] Security: untrusted text cannot reach the planner, errors or the trace; labels never get cleaner.
- [ ] `docs/design.md` updated if a rule or decision changed.
