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

Transcripts and the response cache stay local in `bench/*/runs/`
(git-ignored). The runners write these files on every run; the `meter` in a
summary counts only calls made by the runs that produced the file, so
summaries regenerated from the cache show zero. The spend of the original
runs is in `docs/baselines.md`.
