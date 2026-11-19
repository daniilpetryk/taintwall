# What planners write (stage 2, PR 0)

Measured 2026-11-19, before writing the planner. Question: when a model
writes a CaMeL-style plan, which Python does it use, and how much of it does
the taintwall plan language accept? The answer decides what the language
needs before plan failure rates are measured (contribution D); otherwise the
rates would measure our language, not the models.

## Method

- **Tasks:** the 16 user tasks of AgentDojo banking (v1.2.2). Nothing is
  executed; only the plans are collected.
- **Prompt** ([`plans.py`](../bench/agentdojo/plans.py)): the planner never sees data,
  calls the suite's functions (signatures and docstrings), reads unstructured
  data with `query_reader(question, data, output_schema)` and ends with
  `answer(text)`. Two variants:
  - *natural*: no language rules, to see what models write unprompted;
  - *rules*: the prompt also states the plan language (allowed constructs,
    a list of forbidden ones, "build a new tuple instead of appending").
- **Models** (OpenRouter): Claude Haiku 5.5, DeepSeek V3.2, Gemini 3.1
  Flash Lite, Llama 4 Scout, GPT-5 mini. One plan per task, `max_tokens`
  2000.
- **Census** ([`plan_census.py`](../scripts/plan_census.py)): valid Python,
  accepted by `parse_plan`, the first rejection reason, and the share of
  valid plans that use each construct.
- **Data:** plans and prompts in
  [`bench/results/agentdojo/plans/`](../bench/results/agentdojo/plans/).
  Cost: $0.18 for 160 plans.

## Results

Accepted by `parse_plan` (of 16 plans):

| | Haiku 5.5 | DeepSeek V3.2 | Gemini 3.1 Flash Lite | Llama 4 Scout | GPT-5 mini |
|---|---|---|---|---|---|
| natural | 0% | 12% | 19% | 19% | 0% |
| rules | **75%** | 12% | **94%** | 31% | 38% |

Share of valid plans using a construct, natural → rules (all models, rounded):

| Construct | natural | rules |
|---|---|---|
| method call (`s.lower()`, `d.get()`, `l.append()`) | 38–100% | 0–50% |
| `import` (mostly `datetime`) | 12–78% | 0–12% |
| comprehension | 6–56% | 0–25% |
| f-string format spec (`:.2f`) | 12–89% | 0–31% |
| `def` | 0–56% | 0–31% |
| `a if c else b` | 0–67% | 0–6% |
| `is None` / `is not None` | 0–31% | 0–30% |
| `+=`, `x[i] = v`, unpacking | 0–25% | 0–6% |
| `try`, `lambda`, `class`, `with` | 0–56% | 0% |

With rules, the remaining rejections are: method calls (10 plans: `lower`,
`startswith`, `join`, `get`, `split`), `is` / `is not` (8), format specs (3),
loop `else` (3), `def` (3), `import` (2), and one each of slice,
comprehension and conditional expression. Builtins called beyond ours: `round`, `str`,
`float`, `range` (natural style also `next`, `enumerate`, `sorted`, `isinstance`).

15 of 160 replies have no complete code block (7 natural, 8 rules): the
reply was cut at the 2000-token limit, 13 of them from GPT-5 mini, which
spends the budget on reasoning, and two from Haiku. They count as rejected
above. This is a budget setting, not a language problem.

## Findings

1. **Stating the language is the main lever.** Haiku goes from 0% to 75%
   and Gemini Flash Lite from 19% to 94% with no change to the language.
   The planner prompt (PR 4) must state the rules, as CaMeL's does.
2. **Models differ in how well they follow rules.** DeepSeek V3.2 stays at
   12% (half its plans still call methods), Llama 4 Scout at 31%. These are
   the cheap models contribution D is about; the static checker and typed
   retry (PR 2, PR 4) should turn these violations into fixable errors.
3. **A few constructs survive the rules for every model.** String methods,
   `is None` and number formatting are habits; models keep writing them even
   when told not to. Adding them costs little and is safe.
4. **Acceptance is not correctness.** These numbers say whether a plan
   parses, not whether it does the task; that is PR 5.

## Decisions for PR 1

Add, with labels like the operations they resemble:

- **String methods on strings only:** `lower`, `upper`, `strip`, `startswith`,
  `endswith`, `replace`, `split`, `join`. The result joins the receiver's
  and every argument's deep labels. No attribute access beyond records and
  these names, so no way to reach object internals.
- **`record.get("field", default)`**: like `record["field"]`, plus the
  default's label (and the key's, if computed).
- **`x is None`, `x is not None`**: only against `None`, labeled like `==`.
- **Number format specs** in f-strings: fixed-point only (`:.2f`, `:,.2f`).
- **`a if c else b`**: the result joins `c` and the chosen value; with strict
  flows `c` is a condition like `if`.
- **`x += y`** (and `-=`, `*=`) on a plain name: the same as `x = x + y`.
- **Builtins:** `round`, `str`, `int`, `float`, `range` (bounded by
  `max_items`), `sorted`, `any`, `all`.

Leave out: `import`, `def`, `lambda`, `class`, `try`, mutation
(`append`, `x[i] = v`), slices, loop `else`. With rules, models rarely use
them, and each would add a way to hide control or data flow. Comprehensions
are left for later: with rules they appear in 0–25% of plans, and a filter
inside one is an implicit flow that needs the same care as a loop.

The planner prompt must also give each reply enough output budget (and treat
a cut reply, `finish_reason == "length"`, as a typed failure, not a plan).
