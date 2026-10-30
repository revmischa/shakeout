# Contributing

```console
$ uv sync
$ scripts/check.sh          # every gate CI runs
$ uv run pytest -n auto     # the fast tier (about 10 seconds)
$ uv run pytest -m nightly  # real-library corpus and a 1500-program fuzz campaign
$ scripts/mutation.sh       # mutation testing of the analysis core
```

## Quality gates

Every change must pass all of these:

| Gate | Tool |
|---|---|
| Formatting, and every lint rule (with a short, justified ignore list) | `ruff format`, `ruff check` with `select = ["ALL"]` |
| Types, checked twice | `pyright` strict (plus override, cycle and unnecessary-ignore checks), `mypy --strict` |
| Tests | pytest with warnings as errors, strict markers and config, random order, timeouts |
| Branch coverage ≥ 96% | coverage, including CLI subprocesses |
| Mutation kill rate ≥ 99% (nightly) | mutmut on the effects rules, class analysis and shaker |
| No dead code | vulture, and a test that shaking shakeout removes nothing |
| Spelling, `pyproject.toml` schema | codespell, validate-pyproject |
| Packaging | `uv build`, `twine check --strict`, check-wheel-contents, install smoke test |
| Supply chain and workflows | pip-audit, zizmor (pedantic), actionlint, SHA-pinned actions |
| Docs | `mkdocs build --strict`, plus tests that every CLI flag and config key is documented |

## Adding a case

Behavior is specified by `.case` files in `tests/cases/`: a TOML header with expectations, then
`=== path` sections with the program. Run `uv run pytest --update-goldens`, then review the
generated `.golden` report like code.
