# Changelog

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
