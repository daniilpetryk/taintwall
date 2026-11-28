# Reference results

The baseline every taintwall run is compared against, committed so it is
never lost or silently recomputed: a rerun would cost money, and some
benchmark requests are not deterministic, so its numbers could drift.
Explained in [`docs/baselines.md`](../../docs/baselines.md).

```
<benchmark>/<run name>/
  summary-<mode>[-<attack>[-<channel>]].json   rates with 95% Wilson intervals
  pairs[-<mode>-<attack>-<channel>].jsonl      one line per finished pair
```

Each line of a `pairs*.jsonl` file: `suite_name`, `user_task_id`,
`injection_task_id` (null for utility runs), `attack_type`,
`memory_injection`, `utility`, `security` (true when the attacker's task
succeeded). Lines are sorted, so a rerun that changes nothing changes no
line. AgentDojo's file also has the injection tasks run as user tasks
(`attack_type` null), which AgentDojo checks before attacking.

**[`journal.json`](journal.json)** is the history of measurements, one
entry per patch: what changed, the question, the result and the decision,
and for each measurement the result file and key it comes from (values are
never copied). The site's [measurements page](https://daniilpetryk.github.io/taintwall/measurements.html)
is built from it as patch notes, with each number's change since it was last
measured. A new result file without a journal entry fails
`tests/test_journal.py`; so does a `census.json` that no longer matches its
plans.

`agentdojo/banking-haiku-5.5-draws/` is the AgentDojo baseline over five
draws (`baseline.py --draws 5`, sharing the baseline's cache, so draw 0 is
the baseline itself): summary rates are pooled over the draws, `spread`
gives each per draw, and each draw's pairs are in `pairs.jsonl` (draw 0) and
`pairs-draw-K.jsonl`.

`agentdojo/planner-*/` holds taintwall's own runs (stage 2 on): the same
summaries, with plan failures by cause, and one line per pair with what the
planner did (`failure`, the failed `attempts` as phase and error code,
`answered`, the model `calls` it made, cached or not, and their `cost` in
this run). All settings share one response cache in `bench/agentdojo/runs/cache/`,
so runs of different settings are paired. A run with `--draws N` scores the
suite N times, each pair with its `draw`; its summary rates are pooled over
the draws, and `spread` gives each rate per draw with its mean, lowest and
highest. See [`docs/planner-banking.md`](../../docs/planner-banking.md).

`agentdojo/plans/<run>/` holds a study, not a baseline: the plans five
models wrote for the banking tasks (one JSON line per task with the prompt,
the reply, the extracted code, the finish reason and token counts), the
system prompt used and the functions it offered (`functions.json`). See
[`docs/planner-plans.md`](../../docs/planner-plans.md).

Transcripts and the response cache stay local in `bench/*/runs/`
(git-ignored). The runners write these files on every run; the `meter` in a
summary counts only calls made by the runs that produced the file, so
summaries regenerated from the cache show zero. The spend of the original
runs is in `docs/baselines.md`.
