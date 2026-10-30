# shakeout

A static, tree-shaking bundler for Python. Give it an entry script; it finds every module the
program can import, removes top-level definitions nothing can reach, and writes a self-contained
artifact: one runnable `.py` file, or a pruned directory tree.

```console
$ uvx shakeout build app/main.py -o dist            # single file: dist/main.py
$ uvx shakeout build app/main.py -o dist -t tree    # one shaken file per module
$ uvx shakeout report app/main.py                   # what is kept, and why
$ uvx shakeout why app/main.py pkg.core:helper      # the chain that keeps one symbol
```

Design and roadmap: [docs/PLAN.md](docs/PLAN.md).

## Guarantees

- **Sound by default.** Anything the analysis cannot prove unused is kept: decorators that may
  register, classes whose bases use `__init_subclass__` or metaclasses, modules that call
  `globals()`/`eval`/touch `sys.modules`, module objects passed around as values, star imports.
- **Exact tracebacks without source maps.** Each module is compiled under its original
  project-relative filename with original line numbers (removed code becomes padding), and its
  source is registered with `linecache`. Tracebacks, `warnings`, `inspect.getsource`, logging and
  error trackers show the real file, line, column carets and source text, even where the
  original files do not exist (e.g. AWS Lambda).
- **Self-explaining failures.** If code reaches something that was removed (say, through
  `getattr(sys.modules[...], name)` or generated code), the error carries a note naming the
  definition and the exact `keep` setting that retains it.

## Configuration

```toml
[tool.shakeout]
entry = "app/main.py"
target = "single"          # or "tree"
out = "dist"
keep = ["plugins.alpha", "app.hooks:on_load"]   # modules or module:name reached dynamically
pure-calls = ["app.config.make_settings"]       # calls known to have no side effects
assume-pure-bases = false                       # trust unknown base classes
roots = ["src"]                                 # extra first-party source roots
```

In code, `# shakeout: pure` marks a statement as removable and `# shakeout: keep` pins it.
`importlib.import_module("plugins." + name)` bundles every module under `plugins` (with a
warning), like esbuild's glob imports.

## How it is tested

The suite is the specification and was written before the implementation (`uv run pytest`):

| Tier | What it checks |
|---|---|
| Case catalog (`tests/cases/**/*.case`) | 126 multi-file programs: expected removals, behavioral equivalence of every target (stdout, exit code, traceback frames), a no-shake build, reviewed golden reports |
| Soundness oracle | runs the original under `sys.monitoring`; fails if any function that executed was removed |
| `symtable` differential | scope analysis vs. CPython's symbol table on every stdlib file |
| Unit and property tests | effects rule table, scoping edge cases, runtime loader, line preservation, determinism, idempotence, keep monotonicity |
| Fuzzing | Hypothesis generates multi-module programs with registries, plugins, cycles, local imports and `globals()`; equivalence + soundness |
| Self-hosting | shakeout bundles itself; the bundle reproduces byte-identical output |
| Real-world corpus (nightly) | attrs, click, dateutil, idna, jinja2, packaging, rich, six, tomli vendored and shaken |

Supported: CPython 3.10 to 3.15 (`scripts/matrix.sh`). `uv run pytest -m nightly` runs the
corpus and a 1500-program fuzz campaign.

## Limits (today)

- Targets are `single` and `tree`; zipapp and Lambda packaging, uv-lock staging, `--minify`,
  and multi-entry shared layers are planned (see the plan).
- Extension modules and data files are not bundled; modules reading files via `__file__` get a
  warning.
- Code reached only through strings (plugin registries in settings, generated code) needs `keep`.
