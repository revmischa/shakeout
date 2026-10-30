#!/usr/bin/env bash
# Every quality gate, in the order CI runs them. Usage: scripts/check.sh [--no-network]
set -euo pipefail
cd "$(dirname "$0")/.."

network=1
[[ "${1:-}" == "--no-network" ]] && network=0

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "lockfile is up to date";        uv lock --check
step "formatting";                     uv run ruff format --check .
step "lint (ruff, all rules)";         uv run ruff check .
step "types (pyright strict)";         uv run pyright
step "types (mypy strict)";            uv run mypy
step "spelling";                       uv run codespell src tests/*.py tests/harness tests/unit tests/fuzz/*.py docs README.md
step "dead code (vulture)";            uv run vulture src vulture_whitelist.py --min-confidence 60
step "pyproject.toml schema";          uv run validate-pyproject pyproject.toml
step "tests + branch coverage";        uv run pytest -n auto --cov --cov-report=term-missing:skip-covered
step "docs build (strict)";            uv run --group docs mkdocs build --strict --site-dir build/site >/dev/null
step "package";                        rm -rf dist && uv build -q
uv run twine check --strict dist/*
uv run check-wheel-contents dist/*.whl
step "installed wheel works";          uv run --isolated --no-project --with dist/*.whl shakeout --version
step "workflow lint (actionlint)";     uv run actionlint
if (( network )); then
  step "workflow security (zizmor)";   uv run zizmor --persona=pedantic --quiet .github/workflows
  step "dependency audit (pip-audit)"; uv export --frozen --all-groups --no-hashes --no-emit-project -q -o build/requirements.txt && uv run pip-audit --strict --progress-spinner off -r build/requirements.txt
fi
printf '\n\033[1;32mall checks passed\033[0m\n'
