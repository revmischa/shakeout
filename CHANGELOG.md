# Changelog

## 0.1.1 (2026-11-11)

Fixes found by bundling a real Lambda function.

- Third-party packages are resolved from the project's environment (`--search-path`, `--uv`, an
  activated virtual environment, or the project's `.venv`), never from wherever shakeout is
  installed. Before, an optional import could ship packages from shakeout's own environment:
  one Lambda package shrank from 15.7 MB to 1.6 MB.
- `tree` and `lambda` targets ship data files inside your own packages, such as a vendored
  `certifi/cacert.pem`. Before, HTTPS from the bundle could fail for lack of the CA bundle.

## 0.1.0 (2026-11-08)

First release.

- Static, sound tree shaking of first-party code (and opted-in third-party packages) across
  module boundaries, with esbuild-style rewriting of kept imports.
- Targets: `single` (one runnable or importable `.py`), `pyz` (zipapp), `tree`, and `lambda`
  (reproducible AWS Lambda zip with `--export` handlers).
- Exact tracebacks from bundles: original file names, line numbers and source lines; runtime notes
  that name the `keep` setting when removed code is reached; `shakeout trace`.
- Dependencies: vendoring of unshaken distributions, `include` to shake them, `--uv` staging from
  `uv.lock` for a target Python and platform.
- `--minify` with exact line numbers, `--strip-docstrings`.
- CLI: `build`, `report`, `why`, `trace`; configuration in `[tool.shakeout]`.
- CPython 3.10 to 3.15.
