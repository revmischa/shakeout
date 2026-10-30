#!/usr/bin/env bash
# Mutation testing of the analysis core (effects, classes, graph); fails below the kill-rate floor.
# A surviving mutant means a behavior change no test notices: add a test or remove the code.
set -euo pipefail
cd "$(dirname "$0")/.."
floor="${MUTATION_FLOOR:-99}"
rm -rf mutants
uv run mutmut run --max-children "$(nproc 2>/dev/null || sysctl -n hw.ncpu)" >/dev/null 2>&1 || true
uv run mutmut export-cicd-stats >/dev/null
uv run python - "$floor" <<'PY'
import json, sys
stats = json.load(open("mutants/mutmut-cicd-stats.json"))
total = stats["total"] - stats.get("skipped", 0) - stats.get("no_tests", 0)
rate = 100 * stats["killed"] / max(total, 1)
print(f"mutants: {stats['total']}, killed: {stats['killed']}, survived: {stats['survived']}, "
      f"kill rate: {rate:.1f}% (floor {sys.argv[1]}%)")
sys.exit(0 if rate >= float(sys.argv[1]) else 1)
PY
uv run mutmut results 2>/dev/null || true
