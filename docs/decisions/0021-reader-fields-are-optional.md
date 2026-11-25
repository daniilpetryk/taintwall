# 21. Every reader field is optional; `have_enough_info` is about the question

- **Status:** accepted
- **Date:** 2026-11-25
- **Where:** [#42](https://github.com/daniilpetryk/taintwall/pull/42)

## Context

The first run through the planner ([planner-banking.md](../planner-banking.md), #40) lost 3 of 16 banking tasks (0, 2, 13) to one rule of [ADR 0019](0019-reader-is-a-tool.md): `have_enough_info` had to be true only if the data held every field, so a plan that asked for one field the data lacks (a bill's due date, a landlord's IBAN, a first name in an address change) failed the whole call. The plans already handled the gap: they read those fields with `info.get("date")`, or asked for "an empty string for any field not mentioned". Q4 planned both mechanisms, the flag and optional fields, but only the flag was built.

## Decision

Every field of `output_schema` is optional. The JSON schema still requires each field, as strict structured outputs need, but each may be `null` (`anyOf` with `{"type": "null"}`), and the system prompt tells the model to answer `null` for a field the data does not state and never to guess one. The result leaves a `null` field out: `info.get("field")` is `None` and `info.field` fails while running with `MISSING_FIELD`, labeled with the data. An empty string or an empty list is a value and stays. `have_enough_info` is about the question: the model answers `false` only if the data holds nothing the question asks for, and the call still fails as `TOOL_FAILED`.

There is no syntax for a required field: the planners measured never wrote one, and a plan that needs a field reads it with `info.field`, which fails where it is missing. The text the planner reads (the reader's tool description and the planner prompt) does not change in this PR, so its planner requests are the same as #40's and come from the same cache: the rerun differs only in the reader, and the comparison is paired. Telling the planner that fields may be missing is a separate change to measure.

## Consequences

Which fields are missing is a fact about the data, as their values are; it reaches the plan only inside the result, which carries the data's labels, and a plan that branches on it is under the pc like any other branch on data. A runtime `MISSING_FIELD` goes back to the planner as a typed error with the field's name from the plan itself, where `TOOL_FAILED` did before: the same bit, now from the plan's own read. `scripts/reader_smoke.py` has a fifth case, an invoice that states only some of the fields.

Measured on AgentDojo banking ([planner-banking.md](../planner-banking.md#optional-reader-fields-42)): with retries, utility is back to the plain agent's 11 of 16 and plan failure falls from 19% to 0% alone and from 22% and 25% to 1% and 6% under attack. Retries now change scores, since a program that read a missing field as `info["x"]` is rewritten to use `info.get("x")`; one shot gains less for that reason.
