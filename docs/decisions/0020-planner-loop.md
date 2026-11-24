# 20. The planner loop retries only with typed errors, and never after an effect

- **Status:** accepted
- **Date:** 2026-11-24
- **Where:** [#39](https://github.com/daniilpetryk/taintwall/pull/39)

## Context

Cheap planners write programs that do not run (contribution D, the planner study). A retry helps, but what goes back to the planner must not carry data, which may hold an injection, and running a plan again after it has changed something would do that thing twice (Q3). Q3 makes the number of retries a switch, so the benchmark can compare CaMeL's one shot with retries.

## Decision

`taintwall.planner.Planner` runs a request, building its tools afresh for each one (a factory: the reader's budget and a task's environment belong to one request): it asks the P-LLM for a program as structured output, parses it and runs the static checker, then runs it. A program that cannot run goes back with its typed errors at most `static_retries` times; this loop reads no data. A program that fails while running goes back at most `runtime_retries` times, and only while nothing with an effect has been called in any attempt (the call log is shared); `answer(text)` is an effect, so a plan that has told the user something is never run again. A call that a policy refuses ends the run without a retry: a planner told only that its call failed would still try another way to do what the policy forbids, up to the bound. Policies, like tools, are built afresh for each request. Feedback is the request, the planner's own previous program and its errors as code, line and a name from the program, with the typed errors of up to three attempts before it (a planner that only sees its last mistake may go back to an earlier one): nothing a tool returned. A runtime error depends on data, so the outcome keeps it with its label, as a fresh error without the traceback whose frames held the values the plan read; showing it is the loop's decision, made here by the bound alone.

The system prompt is stable, for prompt caching: the role, the language's rules generated from the core's lists of builtins and string methods, and `Registry.describe()` with `answer` added. The planner's reply is a JSON object with one field, `plan`; a Markdown fence or a common indent around the program is dropped. A reply cut at its budget, or any other model error, ends the run and is kept in the outcome with what it was billed: the budget belongs to the model (`PlannerModel`), and spending more replies on a model that cannot fit its program would not help.

## Consequences

PR 5 runs AgentDojo through this loop and measures utility, plan failure rate and attack success with `Replan(0, 0)` and with retries. Stage 3's policies plug in as `policies`; a refused call is a runtime failure before its effect, so it may be retried. Showing runtime errors only when their label allows it, and richer feedback than typed errors, are later work (design.md, research part).
