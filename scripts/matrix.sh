#!/usr/bin/env bash
# Run the presubmit suite on every supported Python locally (uv downloads interpreters).
set -euo pipefail
for v in 3.10 3.11 3.12 3.13 3.14 3.15; do
  echo "== Python $v"
  UV_PROJECT_ENVIRONMENT=".venv-$v" uv run -q --python "$v" pytest -q -n auto -p no:cacheprovider
done
