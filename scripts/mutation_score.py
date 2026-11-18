# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Fail when the mutation score of the last ``mutmut run`` is below the floor.

Run after ``mutmut run``. The floor is ``min_mutation_score`` in
``[tool.taintwall.quality]`` of ``pyproject.toml``. A mutant is caught when a
test fails (killed) or the run times out; mutants no test reaches count as
survivors, since that code has no test at all.
"""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "mutants" / "mutmut-cicd-stats.json"


def main() -> int:
    """Print the score and survivors; return the exit code."""
    subprocess.run(["mutmut", "export-cicd-stats"], check=True, cwd=ROOT)  # noqa: S607 - dev tool on PATH
    stats = json.loads(STATS.read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floor = float(pyproject["tool"]["taintwall"]["quality"]["min_mutation_score"])
    caught = stats["killed"] + stats["timeout"]
    counted = caught + stats["survived"] + stats["no_tests"] + stats["suspicious"]
    score = 100.0 * caught / counted if counted else 100.0
    print(f"mutation score {score:.1f}% ({caught}/{counted} caught), floor {floor:.1f}%")  # noqa: T201 - CLI output
    if score < floor:
        subprocess.run(["mutmut", "results"], check=False, cwd=ROOT)  # noqa: S607 - dev tool on PATH
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
