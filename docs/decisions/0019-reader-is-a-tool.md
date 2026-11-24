# 19. The reader is a tool; a model call is structured output or a typed error

- **Status:** accepted
- **Date:** 2026-11-24
- **Where:** [#38](https://github.com/daniilpetryk/taintwall/pull/38)

## Context

The planner never reads data; the reader (Q-LLM) does, and the data may carry an injection (contribution A). What the reader returns must be as untrusted as what it read, its reply must not let an injection choose an action, and a reader that lacks the information must say so (Q4). Stage 0 showed how OpenRouter must be called for structured output to be enforced and for the prompt to be cached.

## Decision

The reader is `query_reader(question, data, output_schema)`, an ordinary registry tool built by `reader(client, model, max_calls=...)` in the package `taintwall.reader`, outside `taintwall.tools`, which may not import the LLM layer: the policies of stage 3 need tools, and must never reach a model through them. Labels need no special case: the interpreter joins every argument's deep label into a tool's result, so the reader's output carries the data's sources and readers. Its own spec is public and read-only (the data's readers decide), and its result type is an open record, since the fields are the plan's.

`output_schema` maps field names to `str`, `int`, `float`, `bool` or a one-element list; the reader adds `have_enough_info` and asks for a strict JSON schema with every field required. The reply is checked again locally. The question and the data reach the model as fenced JSON values with `<` and `>` escaped, so no data can close the fence. A reader makes at most `max_calls` calls, since a loop's length can come from data. `have_enough_info: false`, a reply that does not fit, a schema the reader does not know and an exhausted reader all fail the call, which the interpreter reports as `TOOL_FAILED` for `query_reader`, without the reply's text. Q4's `NotEnoughInfo` is therefore a tool failure the planner can see by name, not a code of its own.

`taintwall.llm` has one call, structured output, and its reply follows the request's schema or is an error: `OpenRouterClient` sets `provider.require_parameters`, sends no sampling parameters, marks the system prompt with `cache_control`, checks the reply against the schema itself, and turns a cut reply, a provider error, no answer or a reply off the schema into typed errors raised outside any handler, so neither message nor context carries the reply. `CachedClient` answers a seen request from disk and so never stores a reply off its schema; `BudgetedClient` caps calls and cost.

## Consequences

The planner (PR 4) offers `query_reader` with the other tools, and the static checker already checks its arguments. Sending data to the model provider is not an effect here: the provider is infrastructure, like the planner's. Optional fields (Q4's second mechanism) and a reader that picks from a trusted list (contribution in design.md) come later; so does a schema check at plan time (the checker sees `output_schema` as an open record).
