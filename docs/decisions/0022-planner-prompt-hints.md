# 22. Hints in the planner's prompt about reader fields: measured, not adopted

- **Status:** rejected
- **Date:** 2026-11-25
- **Where:** PR (number set when opened)

## Context

With optional reader fields ([ADR 0021](0021-reader-fields-are-optional.md), #42), retries brought utility on AgentDojo banking back to the plain agent's (11 of 16), but one shot still failed a quarter of its requests ([planner-banking.md](../planner-banking.md)): first programs read a field as `info["x"]` and failed with `MISSING_FIELD`, because nothing the planner reads said a field could be missing. One plan also asked the reader to sum payments, and the reader got the sum wrong 3 times in 9. The review of #43 proposed two hints where the prompt introduces `query_reader`: read every field with `info.get("field")` and handle `None`; and do calculations in the program, asking the reader for the values.

## Decision

Neither hint is adopted; the planner's prompt stays as #42 measured it. Both were run on banking with retries and one shot (each pair of runs shares one cache):

| Planner prompt | Utility, retries | Under attack, retries | Utility, one shot | Under attack, one shot |
|---|---|---|---|---|
| #42, no hints | 11/16 | 87/144, 87/144 | 10/16 | 78/144, 78/144 |
| both hints | 9/16 | 81/144, 81/144 | 8/16 | 72/144, 72/144 |
| `info.get` hint only | 8/16 | 72/144, 72/144 | 6/16 | 54/144, 54/144 |

The `info.get` hint did what it was for: no first program failed with `MISSING_FIELD`. But every change to the planner's prompt draws new programs for all 16 tasks, and the programs drawn differ in ways the hint does not touch. The calculation hint moved matching into the program, where a planner that never sees a transaction filters by the user's IBAN instead of AgentDojo's `"me"` (tasks 7 and 15). Other plans demanded a due date the bill does not state (task 0), or used `list.append`, which the language lacks. Three prompts gave 11, 9 and 8 of 16 with retries. With one program per task, the attacked pairs of a task share it, so the effective sample is 16. Differences of this size are noise between draws of the planner, not an effect of the hints, so none of them can be adopted or ruled out on this data.

## Consequences

A change to the planner's prompt cannot be judged on one draw per task. Judging one needs several programs per task: a sample index in the request, so the cache keeps each draw, and rates over draws. That is a change to the runner, measured first on the prompt as it is, to learn the spread. Until then, prompt changes wait. The runs are kept as a study in the journal (`planner-prompt-hints`).
