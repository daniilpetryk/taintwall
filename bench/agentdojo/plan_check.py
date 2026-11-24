# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Run the static plan checker over collected plans, without running them.

Reads the JSON lines written by ``plans.py`` (one file per model). Every plan
the plan language accepts is checked against the AgentDojo banking tools and
the two the planner prompt adds: the real reader (``taintwall.reader``) and ``answer``; the report
counts, per model, the plans that pass the checker too and the codes of what
it finds in the rest. Nothing is executed and no model is called.

Usage (from the repo root)::

    make bench-check RUN=rules
"""

import json
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite
from registry import spec_for

from taintwall.core.errors import PlanError
from taintwall.core.plan import parse_plan
from taintwall.core.syntax import LANGUAGE_VERSION
from taintwall.reader import SPEC as READER
from taintwall.tools import READ_ONLY, Param, Scalar, ToolSpec, check_plan

ANSWER = ToolSpec("answer", "Tell the user.", (Param("text", Scalar.STR),), Scalar.NONE, READ_ONLY)


def banking_specs() -> dict[str, ToolSpec]:
    """The tools the study's planners were offered."""
    suite = get_suite("v1.2.2", "banking")
    specs = {function.name: spec_for(function, "banking") for function in suite.tools}
    return {**specs, READER.name: READER, ANSWER.name: ANSWER}


def counts(plans: list[dict[str, object]], specs: Mapping[str, ToolSpec]) -> dict[str, object]:
    """Per model row: plans, those the language accepts, those the checker passes, codes found."""
    accepted = checked = 0
    codes: Counter[str] = Counter()
    for plan in plans:
        code = plan["code"]
        if not isinstance(code, str):
            continue
        try:
            parsed = parse_plan(code)
        except PlanError:
            continue
        accepted += 1
        result = check_plan(parsed, specs)
        checked += result.ok
        # Each code once per plan: how many plans have it, not how often.
        codes.update({error.code.value for error in result.errors})
    return {
        "plans": len(plans),
        "accepted": accepted,
        "checked": checked,
        "codes": dict(sorted(codes.items())),
    }


def check_run(directory: Path) -> dict[str, object]:
    """The checker's counts for every model of a run, as ``check-v<language>.json``."""
    specs = banking_specs()
    models = {}
    for path in sorted(directory.glob("*.jsonl")):
        plans = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        models[path.stem.replace("__", "/")] = counts(plans, specs)
    return {"language": LANGUAGE_VERSION, "models": models}


def main() -> int:
    """Print the checker's counts for a run and write them next to its plans."""
    directory = Path(sys.argv[1])
    data = check_run(directory)
    out = directory / f"check-v{LANGUAGE_VERSION}.json"
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data, indent=2))  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":
    sys.exit(main())
