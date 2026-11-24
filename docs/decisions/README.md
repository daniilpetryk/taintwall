# Decision log

Dated decisions with their reasons, oldest first ([ADR 0001](0001-record-decisions.md)). The
current design is [`design.md`](../design.md); these files say when each rule appeared and why. Newer
decisions refine older ones rather than editing them.

| ADR | Decision | Date | PRs | Status |
|---|---|---|---|---|
| [0001](0001-record-decisions.md) | Record decisions as dated ADRs | 2026-11-19 | [#28](https://github.com/daniilpetryk/taintwall/pull/28) | accepted |
| [0002](0002-initial-design.md) | Initial design: CaMeL base, four contributions, decisions Q1–Q13 | 2026-11-17 | [`cb73c17`](https://github.com/daniilpetryk/taintwall/commit/cb73c17), [`629c7c9`](https://github.com/daniilpetryk/taintwall/commit/629c7c9) | accepted |
| [0003](0003-craibench-and-own-tasks.md) | Run on CrAIBench and add own anvil tasks (Q13 outcome) | 2026-11-17 | [`d5c9f94`](https://github.com/daniilpetryk/taintwall/commit/d5c9f94) | accepted |
| [0004](0004-branches.md) | Branches: work on dev, release to main | 2026-11-17 | [`5a05129`](https://github.com/daniilpetryk/taintwall/commit/5a05129) | accepted |
| [0005](0005-minted-trust.md) | Trusted provenance only through a mint capability | 2026-11-18 | [#12](https://github.com/daniilpetryk/taintwall/pull/12) | accepted |
| [0006](0006-typed-errors.md) | Errors to the planner are typed codes; tool text is dropped | 2026-11-18 | [#13](https://github.com/daniilpetryk/taintwall/pull/13) | accepted |
| [0007](0007-clean-code-in-ci.md) | Clean-code rules are enforced in CI, not by review | 2026-11-18 | [#14](https://github.com/daniilpetryk/taintwall/pull/14) | accepted |
| [0008](0008-benchmark-projects.md) | Benchmarks in their own uv projects; results committed | 2026-11-18 | [#15](https://github.com/daniilpetryk/taintwall/pull/15), [#18](https://github.com/daniilpetryk/taintwall/pull/18) | accepted |
| [0009](0009-provider-errors-and-memory-baseline.md) | Provider errors are failures, not attack successes; memory injection is the headline baseline | 2026-11-18 | [#18](https://github.com/daniilpetryk/taintwall/pull/18) | accepted |
| [0010](0010-strict-implicit-flows.md) | Strict implicit flows by default | 2026-11-19 | [#24](https://github.com/daniilpetryk/taintwall/pull/24) | accepted |
| [0011](0011-labeled-failures.md) | A failure carries the label of everything it depends on | 2026-11-19 | [#24](https://github.com/daniilpetryk/taintwall/pull/24) (three review rounds) | accepted |
| [0012](0012-trace-shape-is-data.md) | The trace's shape is data | 2026-11-19 | [#24](https://github.com/daniilpetryk/taintwall/pull/24) | accepted |
| [0013](0013-language-by-measured-need.md) | The plan language grows by measured need | 2026-11-19 | [#26](https://github.com/daniilpetryk/taintwall/pull/26) | accepted |
| [0014](0014-measurement-journal.md) | Measurements are a journal of patches; numbers stay in result files | 2026-11-19 | [#27](https://github.com/daniilpetryk/taintwall/pull/27) | accepted |
| [0015](0015-versions-and-changelog.md) | Semantic versions per release; the changelog is the release notes | 2026-11-19 | [#28](https://github.com/daniilpetryk/taintwall/pull/28) | accepted |
| [0016](0016-language-versions.md) | The plan language has versions; a plan census records its version | 2026-11-19 | [#29](https://github.com/daniilpetryk/taintwall/pull/29) | accepted |
| [0017](0017-tools-from-trusted-specs.md) | Tools come from trusted specs; a hook sees every call before it runs | 2026-11-20 | [#30](https://github.com/daniilpetryk/taintwall/pull/30) | accepted |
| [0018](0018-static-checker-refuses-only-sure-failures.md) | The static checker refuses only what would fail whatever the data | 2026-11-24 | [#31](https://github.com/daniilpetryk/taintwall/pull/31) | accepted |
