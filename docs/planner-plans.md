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
- **Models** (OpenRouter, each provider's default temperature): Claude Haiku
  5.5, DeepSeek V3.2, Gemini 3.1 Flash Lite, Llama 4 Scout, GPT-5 mini. One
  plan per task and variant, so each rate is out of 16 and carries a 95%
  Wilson interval.
- **Budget:** 8000 output tokens per reply, reasoning included. A first run
  with 2000 cut 15 of 160 replies, almost all from hidden reasoning (a cut
  Haiku reply had 774 visible characters and 2000 tokens), so it was
  discarded. Here no reply was cut (`finish_reason` is `stop` for all 160).
  Largest replies: GPT-5 mini 4653 tokens (up to 3136 of them reasoning),
  DeepSeek 2748, Haiku 2374; Gemini and Llama stay under 400.
- **Census** ([`plan_census.py`](../bench/agentdojo/plan_census.py), `make bench-census`):
  valid Python, accepted by `parse_plan`, the first rejection reason, and the
  share of valid plans that use each construct. Builtins are told apart from
  the offered functions by the run's `functions.json`.
- **Data:** plans with finish reasons and token counts, prompts and function
  lists in [`bench/results/agentdojo/plans/`](../bench/results/agentdojo/plans/).
  Cost: $0.21 for 160 plans.

## Results

Accepted by `parse_plan`, out of 16 plans (95% Wilson interval):

| | Haiku 5.5 | DeepSeek V3.2 | Gemini 3.1 Flash Lite | Llama 4 Scout | GPT-5 mini |
|---|---|---|---|---|---|
| natural | 0% (0–19) | 12% (3–36) | 25% (10–49) | 31% (14–56) | 0% (0–19) |
| rules | **88%** (64–97) | 19% (7–43) | **81%** (57–93) | 44% (23–67) | 62% (39–82) |

All 160 plans are valid Python. Share of valid plans using a construct,
natural → rules (range over the five models):

| Construct | natural | rules |
|---|---|---|
| method call (`s.lower()`, `d.get()`, `l.append()`) | 38–100% | 0–44% |
| `import` (mostly `datetime`) | 6–94% | 0–6% |
| comprehension | 19–62% | 0–25% |
| f-string format spec (`:.2f`) | 19–88% | 0–50% |
| `def` | 0–75% | 0–25% |
| `try` | 0–75% | 0% |
| `a if c else b` | 0–56% | 0–19% |
| `is None` / `is not None` | 0–38% | 0–19% |
| `+=`, `x[i] = v`, `a, b = ...` | 0–38% | 0–6% |
| `lambda` | 0–19% | 0–6% |

With rules, the first rejection reasons are: method calls (10 plans:
`lower`, `join`, `append`, `startswith`, `get`), format specs (6), `is` (5),
`def` (3), comprehensions (3), conditional expressions (2), and one each of
slice, import, tuple loop target and loop `else`. Builtins beyond ours used
with rules: `int` (3), `round` (2), and once each `range`, `sorted`, `str`,
`float`, `bool`, `next`, `exit`, `locals`. In natural style also `any` (8),
`isinstance` (7), `getattr`, `enumerate`, `defaultdict`, `Counter`, `zip`.

## Findings

1. **Stating the language is the main lever.** Haiku goes from 0% to 88%,
   GPT-5 mini from 0% to 62% and Gemini Flash Lite from 25% to 81% with no
   change to the language (non-overlapping intervals for Haiku and GPT-5
   mini). The planner prompt (PR 4) must state the rules, as CaMeL's does.
2. **Some models follow rules much less.** DeepSeek V3.2 stays at 19% (44%
   of its plans still call methods and half use format specs), and Llama 4
   Scout at 44%. With 16 plans the middle of the ranking is not resolved
   (Llama, GPT-5 mini and Gemini overlap), but Haiku and DeepSeek differ
   clearly. Cheap models like these are what contribution D is about: the
   static checker and typed retry (PR 2, PR 4) should turn their violations
   into fixable errors.
3. **A few constructs survive the rules.** String methods, number formatting
   and `is None` remain the most common rejections; they are habits, cheap
   to support, and safe to label.
4. **Acceptance is not correctness.** These numbers say whether a plan
   parses, not whether it does the task; that is PR 5.
5. **Budget.** Reasoning models need room: at 2000 output tokens, Haiku and
   GPT-5 mini replies were cut before the code. The planner client must set
   a per-model budget and treat `finish_reason == "length"` as a typed
   failure, not as a plan.

## Follow-up: the same plans under language version 2

Stage 2, PR 1a added the constructs below (#29). Recounting the same plans,
with no new model calls (`census-v2.json` next to the study's `census-v1.json`):

| | Haiku 5.5 | DeepSeek V3.2 | Gemini 3.1 Flash Lite | Llama 4 Scout | GPT-5 mini |
|---|---|---|---|---|---|
| natural, v1 → v2 | 0% → 19% | 12% → 25% | 25% → 75% | 31% → 38% | 0% → 0% |
| rules, v1 → v2 | 88% → **100%** | 19% → **88%** | 81% → 88% | 44% → 44% | 62% → **94%** |

What still fails with rules: function definitions (3 plans), comprehensions
(4), slices (2), and one each of an unlisted method, a width format, an
import, a tuple loop target and a loop `else`. Llama 4 Scout writes most of
them.

## Decisions for PR 1

Add, with labels like the operations they resemble:

- **String methods on strings only:** `lower`, `upper`, `strip`, `startswith`,
  `endswith`, `replace`, `split`, `join`. The result joins the receiver's
  and every argument's deep labels. No attribute access beyond records and
  these names, so no way to reach object internals.
- **`record.get("field", default)`**: like `record["field"]`, plus the
  default's label (and the key's, if computed).
- **Number format specs** in f-strings: fixed-point only (`:.2f`, `:,.2f`).
- **`x is None`, `x is not None`**: only against `None`, labeled like `==`.
- **`a if c else b`**: the result joins `c` and the chosen value; under
  strict flows `c` is a condition like `if`.
- **`x += y`** (and `-=`, `*=`) on a plain name: the same as `x = x + y`.
- **Builtins:** `round`, `int`, `str`, `float`, `bool` (conversions, which the
  data shows with and without rules), `range` (bounded by `max_items`),
  `sorted`, and `any`/`all` (the most common natural-style builtins we lack).

Leave out:

- `import`, `def`, `lambda`, `class`, `try`, mutation (`append`,
  `x[i] = v`), slices, loop `else`. With rules, models rarely use them, and
  each would add a way to hide control or data flow.
- `print`, `exit`, `locals`, `isinstance`, `getattr`: `answer` replaces
  printing, a plan ends by itself, and the rest reach into the runtime.
- Comprehensions, for now: with rules they appear in 0–25% of plans, and a
  filter inside one is an implicit flow that needs the same care as a loop.
