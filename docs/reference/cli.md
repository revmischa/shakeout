# Command line

```text
shakeout build  [ENTRY] [options]   write a bundle
shakeout report [ENTRY] [options]   show every statement, kept or removed, and why
shakeout why    [ENTRY] MOD:NAME    explain why a symbol is kept (exit 1 if it was removed)
shakeout trace  FILE --manifest M   add original source lines and removal notes to a traceback
shakeout --version
```

`ENTRY` defaults to `entry` in `[tool.shakeout]`. Command-line values are added to list options
from the configuration file and override scalar ones. Exit code 2 means a usage or build error.

## Analysis options

These apply to `build`, `report` and `why`.

| Option | Meaning |
|---|---|
| `--keep MOD[:NAME]` | Always keep a module (whole) or one definition. Repeatable |
| `--include PKG` | Bundle and shake this third-party package. `'*'` means all. Repeatable |
| `--export NAME` | An entry function called from outside, such as a Lambda handler. Makes the entry a library module. Repeatable |
| `--pure-call QUALNAME` | Treat calls to this function as side-effect free. Repeatable |
| `--assume-pure-bases` | Assume unknown base classes don't register subclasses |
| `--root DIR` | Extra first-party source root. Repeatable |
| `--search-path DIR` | Extra directory with third-party packages. Repeatable |
| `--uv` | Install the locked dependencies from `uv.lock` for the target, and use them |
| `--python-version X.Y` | Target Python for `--uv` |
| `--platform TRIPLE` | Target platform for `--uv`, e.g. `x86_64-manylinux_2_28` |
| `--no-shake` | Bundle everything that is imported, without removing code |
| `--config PATH` | The `pyproject.toml` to read. By default shakeout searches upwards from the entry |

## build options

| Option | Meaning |
|---|---|
| `--out DIR`, `-o` | Output directory. Default `dist/` |
| `--target`, `-t` | `single` (default), `tree`, `pyz` or `lambda`. See [Targets](../concepts/targets.md) |
| `--minify` | Drop comments and blank lines. Line numbers stay exact |
| `--strip-docstrings` | Also drop docstrings, like `python -OO` |
| `--no-embed-source` | Don't show source lines in tracebacks. Restore them with `shakeout trace` |
| `--no-tombstones` | Don't explain removed names when they are accessed at runtime |
| `--no-copy-dependencies` | `tree`, `pyz` and `lambda` targets: don't ship third-party packages that aren't shaken |

## report options

| Option | Meaning |
|---|---|
| `--json` | Machine-readable output: bundled and dropped modules, kept and removed symbols, diagnostics |

## trace options

| Option | Meaning |
|---|---|
| `FILE` | The traceback text, or `-` for stdin |
| `--manifest PATH` | The build manifest: `shakeout-manifest.json`, or `<entry>.manifest.json` for `single` |
| `--root DIR` | The original source tree. Default: the current directory |
