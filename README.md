# shakeout

A static, tree-shaking bundler for Python. Give it an entry script: it finds every module the
program can import, removes top-level code nothing can reach, and writes a self-contained
artifact. That can be one runnable `.py`, a zipapp, a pruned directory or a reproducible AWS
Lambda zip. Tracebacks from bundles name the original files and lines.

```console
$ uvx shakeout build app/main.py -o dist                       # one runnable file
$ uvx shakeout build handler.py -t lambda --export handler     # Lambda package
$ uvx shakeout report app/main.py                              # what is kept, and why
$ uvx shakeout why app/main.py pkg.core:helper                 # the chain that keeps a symbol
```

**Documentation:** https://revmischa.github.io/shakeout/ (sources in [`docs/`](docs/); the
design and roadmap are in [docs/project/design.md](docs/project/design.md)).

## Guarantees

- **Sound by default.** Anything the analysis can't prove unused is kept: registration
  decorators, `__init_subclass__` and metaclass registries, modules that use `globals()`, `eval`
  or `sys.modules`, module objects passed around as values, star imports.
- **Exact tracebacks without source maps.** Modules keep their original file names and line
  numbers, and their sources are registered with `linecache`. Tracebacks, `warnings`,
  `inspect.getsource` and error trackers show real files, lines, carets and code, even in
  Lambda.
- **Self-explaining failures.** Reaching a removed name at runtime raises the usual exception
  with a note that names the `keep` setting to add.

## How it is tested

| Tier | What it checks |
|---|---|
| Case catalog (`tests/cases/**/*.case`) | 126 multi-file programs: expected removals, behavioral equivalence of every target (stdout, exit code, traceback frames), a no-shake build, reviewed golden reports |
| Soundness oracle | runs the original under `sys.monitoring`; fails if any function that executed was removed |
| Differential checks | scope analysis vs. CPython's `symtable`, and minified vs. original bytecode, over the whole stdlib |
| Fuzzing | Hypothesis-generated multi-module programs; equivalence + soundness |
| Mutation testing | 100% of mutants in the effects rules, class analysis and shaker are killed (floor 99%) |
| Real-world corpus (nightly) | attrs, click, dateutil, idna, jinja2, packaging, rich, six, tomli |
| Self-hosting | shakeout bundles itself byte-identically and contains no dead code |

Every gate CI runs is in `scripts/check.sh`: `ruff` with all rules, strict `pyright` and `mypy`,
96% branch coverage, vulture, codespell, packaging checks, pip-audit, zizmor and actionlint, and
a strict docs build. CPython 3.10 to 3.15 are supported (`scripts/matrix.sh`).
