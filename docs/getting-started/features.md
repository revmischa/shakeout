# Features

## Bundling

- **Targets.** `single` gives one `.py`, `pyz` a zipapp, `tree` a pruned directory and `lambda` a
  reproducible AWS Lambda zip. See [Targets](../concepts/targets.md).
- **Library entries.** `--export handler` keeps a function called from outside (a Lambda handler
  or a plugin hook). The entry is then imported as a module, not run as `__main__`.
- **Dependencies.** Third-party packages are shipped whole in tree-shaped targets, or bundled and
  shaken with `include`. `--uv` installs the versions pinned in `uv.lock` for a target platform.

## Shaking

- Unused functions, classes, constants and imports are removed across modules, and so are
  modules that nothing reachable imports.
- Kept `from m import a, b` statements drop the names nobody uses.
- `importlib.import_module("plugins." + name)` bundles every module under `plugins`, like
  esbuild's glob imports.

## Safety and debugging

- Conservative effect analysis, explained in [Side effects](../concepts/side-effects.md).
- Exact tracebacks: original file names, line numbers, carets and source text.
- Removed names reached at runtime fail with a note that names the `keep` setting to add.
- `minify` keeps line numbers exact. `shakeout trace` restores the original lines of a
  traceback.
- A build manifest records the build ID, modules, removals and the Lambda handler.
