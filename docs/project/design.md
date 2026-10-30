# shakeout — design and delivery plan

Status: draft for review · 2026-10-30
Working name: **shakeout** (free on PyPI; alternatives: `secateurs`, `defoliate`, `leafdrop`). Renaming is a find/replace until Phase 1 ships.

## Status (2026-10-30)

| Phase | State | Notes |
|---|---|---|
| 0. Test foundation | done | Harness, oracles (validated against broken-bundler stubs), 126 cases. The case format became one `.case` file per case (TOML header + `=== path` sections) instead of a directory; much faster to write and review. |
| 1. Bundler without shaking | done | `single`, `tree`, `pyz`, `lambda`; line preservation; linecache registration; manifest. |
| 2. Analysis | done | Scopes (stdlib-wide `symtable` differential), effects, dynamic detection. |
| 3. Shaking | done | Plus esbuild-style import rewriting: kept `from m import a, b` drops unused names. |
| 4. Diagnostics | done | Tombstone `__getattr__`, runtime notes, `why`, `report`, `trace`. `--debug` stubs were dropped: tombstones and notes cover the same need. |
| 5. Dependencies, uv, Lambda | done | `--uv` lock staging with target python/platform, vendoring of unshaken distributions, `include`, library entries (`exports`), reproducible Lambda zip with an invocation test. A Docker-based Lambda emulator test is still open. |
| 6. Hardening | done | Fuzzer, mutation testing (100% kill), self-hosting and dead-code dogfooding, 3.10-3.15 matrix, strict lint/types, coverage gate, supply-chain checks. Open: running libraries' own test suites against shaken trees; benchmarks. |
| 7. Later | partly | `--minify`/`--strip-docstrings` done. Open: multi-entry shared layers, `hatch-shakeout`, GitHub Action. |

Bugs found by the oracles so far, each now a regression test: names required by kept
`from`-imports were removed (fuzz), imports inside whole modules lost their targets (fuzz),
`global` in a nested function resolved to the enclosing local (case catalog), sources with form
feeds shifted line numbers (3.10 stdlib sweep), the pre-3.13 C excepthook bypassing linecache
(version matrix), a dead constant in shakeout itself (self-hosting), a cached analysis that
hid mutants, `keep = ["pkg:submodule"]` not discovering the submodule, and silent `keep`/`export`
of undefined names (mutation testing).

Corpus results (pure-Python libraries, each driven by a small script exercising its main API):

| Library | Modules shipped / reachable | Top-level statements kept | Bytes saved |
|---|---|---|---|
| rich | 91 / 166 | 1064 / 1870 | 25% |
| packaging | 10 / 13 | 150 / 248 | 27% |
| click | 16 / 16 | 473 / 535 | 14% |
| jinja2 (+markupsafe) | 24 / 25 | 693 / 764 | 5% |
| attrs | 19 / 19 | 236 / 253 | 4% |
| dateutil, idna, six, tomli | all | 98-100% | ~0% |

Libraries whose public API imports everything eagerly gain little; libraries with lazy or
optional subsystems (rich, packaging) gain a lot. Statement-level shaking inside large
first-party codebases, and opting in heavy dependencies, are where the remaining upside is.

---

## 1. Summary

shakeout is a static, tree-shaking bundler for Python. Given an entry point (a script, a Lambda handler, a console script), it finds every module the program can import, works out which top-level definitions are actually reachable, removes the rest, and emits a self-contained artifact: a single `.py`, a `.pyz` zipapp, a pruned directory tree, or an AWS Lambda zip.

Its two non-negotiable properties:

1. **Sound by default.** If the analysis cannot prove something is unused, it is kept. A smaller bundle is never worth a behavior change. Soundness is enforced by tests that watch the original program execute and fail if anything that ran was removed (§9.5).
2. **Debuggable in production.** A traceback from a bundle points at the original file, line and column, shows the original source line, and explains removed code when that is the cause. No separate source map is needed to read it (§7).

The first deliverable is the test suite, not the bundler (§10, Phase 0).

### Goals

- Function/class/assignment-level dead-code elimination across module boundaries, for first-party code and opted-in third-party pure-Python packages.
- Module-level elimination: a module nothing reachable imports is not shipped.
- Exact tracebacks from bundles; clear errors when removed code is reached.
- Reproducible, deterministic output driven by `uv.lock`.
- First-class AWS Lambda target, since that is where size and cold start hurt most.

### Non-goals (v1)

- Scope hoisting (merging all modules into one namespace like esbuild/Rollup). Python module objects, circular imports and `import a.b` semantics make this unsafe for real code; per-module isolation preserves semantics and keeps tracebacks honest.
- Shaking inside class bodies (unused methods). Attribute access on instances is not statically resolvable.
- Shaking compiled extensions. `.so`/`.pyd` files are shipped whole or excluded.
- Replacing PyInstaller/Nuitka for freezing an interpreter.

---

## 2. Landscape

| Tool | What it removes | How |
|---|---|---|
| PyInstaller, Nuitka | Nothing below module level | Static import following |
| pyodide-pack | Whole unused modules | Runtime tracing on sample runs; can miss untraced paths |
| stickytape, pinliner | Nothing | Concatenates modules into one file |
| vulture | Nothing (reports only) | Static dead-code report |
| python-minifier | Whitespace, docstrings, renames | Single-file AST rewrite |

No existing tool does cross-module, symbol-level elimination. The discuss.python.org "Package Pruning" thread (2024) asked for exactly this and concluded no tool exists.

---

## 3. JavaScript concepts mapped to Python

| JS bundler concept | shakeout equivalent | Notes |
|---|---|---|
| Static ESM `import` | `import` / `from … import` statements | Python imports run code, so effects analysis is mandatory, not optional |
| `"sideEffects": false` in package.json | Computed per module; overridable per package in `[tool.shakeout.packages.<name>] side-effects = false` | Computed by default, declared only as an override |
| `/* @__PURE__ */` | `# shakeout: pure` on a statement | Also `--pure-call qualified.name` |
| Entry points | `--entry script.py`, `--export pkg.mod:func` (Lambda handler), `[project.scripts]` | Several entries per build allowed |
| Externals | Everything not bundled; default: stdlib + third-party not opted in | `--include pkg` opts a dependency into shaking |
| Dynamic `import()` | `importlib.import_module("x")` | Constant arg: followed. Computed arg: warning + `--keep-module` |
| Code splitting / shared chunks | Multi-entry builds emit a shared layer | Maps naturally to Lambda Layers (Phase 7) |
| Source maps | Original filename/line/column baked into code objects + build manifest | See §7 for why Python needs less than JS here |
| Minification | `--minify`: drop docstrings/comments, compile from AST | Positions preserved via AST, verified on 3.14 |
| Bundle analyzer | `shakeout report`, `shakeout why mod:name` | Text and JSON output |

---

## 4. Architecture

The work splits into pipeline stages. Each stage is a pure function over immutable data, so it can be tested on its own.

```
 Environment ─► Resolve ─► Parse ─► Analyze ─► Link ─► Shake ─► Emit ─► Verify
  (uv.lock,      (module    (AST,    (scopes,   (cross-   (reach-   (format    (compile,
   sys.path,     graph,     source   bindings,  module    ability,  writers,   smoke run,
   config)       no exec)   text)    refs,      ref       reasons)  runtime    manifest)
                                     effects)   graph)              loader)
```

### 4.1 Core data model (the IR)

- `ModuleInfo`: name, origin path, is_package, source text, kind (`first_party | third_party | stdlib | extension | namespace`), bundled flag.
- `TopLevelStatement`: stable ID `pkg.mod@L12-L20`, line span including decorators and leading comments, AST nodes (statements sharing a line are grouped into one), and:
  - `binds: name → [Binding]`, where a Binding is a local value or an import target (`Module(x)` / `From(x, attr)`);
  - `refs: [Ref(base, attr_chain)]`, scope-correct. Function locals are excluded, `global` is respected, and comprehension scoping is handled;
  - `loads`: modules this statement may import, whether eagerly or inside functions;
  - `effect: Pure | Impure(reason) | DependsOn(modules)`;
  - `flags`: `root`, `dead` (a non-entry `__main__` guard), `dynamic(reason)`.
- `Resolution`: following `pkg.sub.func` through re-exports gives the traversed statements, the terminal binders, and any modules that escape as whole values.
- `ShakeResult`: live statements, loaded modules, and a **reason graph** (why each statement is kept). The `why` command and goldens use the reason graph.

### 4.2 Code layout

```
src/shakeout/
  config.py            # [tool.shakeout] schema, CLI merge, validation
  environment/         # uv.lock → staging env; search paths; stdlib detection
  resolve/             # import resolution without executing code (PathFinder)
  analysis/
    scopes.py          # scope-correct ref/binding collection
    bindings.py        # top-level statement grouping + binds/refs/loads
    effects.py         # purity rules, pure-call registry, class registration rules
    dynamic.py         # globals()/eval/sys.modules/import_module detection
  graph/
    link.py            # cross-module resolution with re-export chasing
    shake.py           # worklist reachability + reason graph
    explain.py         # `why`, report data
  emit/
    chunks.py          # line-preserving source chunks; minify via AST
    single_file.py  zipapp.py  tree.py  lambda_zip.py
    runtime/loader.py  # the in-bundle importer (also tested standalone)
  diagnostics/
    manifest.py  tombstones.py  trace.py
  cli.py  api.py       # api.py is the stable library surface for adapters
```

### 4.3 Effects model (what counts as safe to drop)

A top-level statement can be dropped only if it is classified **Pure** and nothing reachable references it.

| Construct | Pure when |
|---|---|
| `def` / `async def` | No decorators, or all decorators in the pure registry (`dataclass`, `lru_cache`, `cache`, `property`, `staticmethod`, `classmethod`, `wraps`, `overload`, `final`, `contextmanager`, `abstractmethod`, `total_ordering`, …); default values are pure expressions |
| `class` | Decorators pure; no class keywords (`metaclass=`, `__init_subclass__` kwargs); body contains only pure statements; every base is a builtin, a whitelisted stdlib/typing base, or a bundled class that is not *registering*. A class is registering if it defines `__init_subclass__`, has a metaclass, or inherits from one that does. Unknown third-party bases are impure unless `--assume-pure-bases` |
| `x = expr` / `x: T = expr` | Targets are plain names; `expr` is a pure expression: literals, names, attribute/subscript of pure, operators, lambdas, comprehensions over pure iterables, calls to the pure-call registry (`frozenset`, `TypeVar`, `namedtuple`, `re.compile`, `field`, `getLogger`, …) |
| `import` / `from … import` | Every module it eagerly loads is effect-free: stdlib (minus a short denylist) or a bundled module whose own statements are all Pure (least fixpoint, handles cycles). Third-party externals are impure since importing them may fail or have effects |
| `if TYPE_CHECKING:` | Always pure |
| `if __name__ == "__main__":` | Root in the entry module; dead elsewhere |
| `type X = …` | Pure (lazy) |
| Anything else (`for`, `try`, `with`, bare calls, `del`, augmented assignment, attribute/subscript stores) | Impure root |

Always-kept roots in any loaded module: impure statements, `from __future__` imports, module docstrings (unless `--minify`), dunder bindings (`__all__`, `__getattr__`, `__dir__`, `__version__`), and statements marked `# shakeout: keep`.

Whole-module escapes, where the entire module is kept: the module calls `globals()`, `vars()`, `locals()`, `eval`, `exec`, `__import__`, or touches `sys.modules`; it is the source of a star import; or a module object is used as a value rather than through attribute access, e.g. passed to `getattr` or stored.

Every rule in this table becomes a row in a table-driven test (§9.1) before it is implemented.

---

## 5. Output targets

| Target | Contents | Use |
|---|---|---|
| `single` | One `.py`: runtime loader + per-module source chunks + entry | Scripts, copy-paste deploys, CI tools |
| `pyz` | zipapp with shaken tree + `__main__.py` | CLIs; zipimport gives real per-module files in tracebacks |
| `tree` | Directory mirroring import paths, one shaken file per module, plus kept `dist-info` | Containers, debugging, and the corpus test harness (§9.6) |
| `lambda` | `tree` zipped, handler export wrapper, optional layer split | AWS Lambda |

Extension modules (`.so`) cannot load from a single file or zipimport. `single` refuses with a clear error if one is required. `pyz` can extract to a cache directory (opt-in). `tree` and `lambda` ship them as is.

Entry kinds:
- **Script.** Runs as `__main__`.
- **Export.** `pkg.mod:func` is a root, and the artifact exposes it at a stable import path, e.g. Lambda handler `bundle.handler`.
- **Console script.** Read from `[project.scripts]`.

---

## 6. Dependencies and uv integration

### 6.1 uv has no plugin API

As of uv 0.13 (October 2026) uv exposes no third-party plugin interface, and the `uv_build` backend has no build or metadata hooks; issue astral-sh/uv#14561 requests them. A "uv plugin" in the Poetry sense is therefore not possible today.

### 6.2 Integration design

1. **Standalone CLI distributed for uv.** `uvx shakeout build` or `uv tool install shakeout`. It reads `[tool.shakeout]` from `pyproject.toml`, which is the same file uv uses, so it feels native.
2. **uv as the environment engine.** shakeout never resolves dependencies itself. For a build it runs
   `uv export --frozen --no-dev --no-hashes` then `uv pip install --target <staging> --python-platform <target> --python-version <ver>`.
   That honors `uv.lock` exactly, and the platform flags allow cross-building a Linux Lambda bundle from macOS. The staging tree is what gets analyzed.
3. **A stable library API** (`shakeout.api.build(config) -> BuildResult`) so thin adapters are cheap. Candidates: a `hatch-shakeout` build hook (works today, and `uv build` drives hatchling), a pre-commit hook, a GitHub Action, and a uv adapter if uv ever adds plugins.
4. **`uv run` workflow.** `uv run shakeout dev app.py` builds in debug mode (§7.4) and executes, for fast feedback.

Example config:

```toml
[tool.shakeout]
entry = ["app/handlers.py:handler"]
target = "lambda"
python = "3.13"
platform = "x86_64-manylinux_2_28"
include = ["requests", "urllib3", "idna", "charset_normalizer"]   # opt-in shaking
keep = ["app.plugins:*"]                                          # reached dynamically
pure-calls = ["structlog.get_logger"]

[tool.shakeout.packages.attrs]
side-effects = false          # assertion, verified against analysis; warns on conflict
```

### 6.3 Third-party policy

- Third-party packages are external (shipped whole, unshaken) unless listed in `include`. Shaking dependencies is the biggest win but also the riskiest, so it is opt-in per package and covered by the corpus tests (§9.6).
- `dist-info` is kept for every shipped distribution. `importlib.metadata.version("x")` is common at import time.
- Packages that read data files through `__file__`/`importlib.resources` are detected. Their non-Python files are shipped in `tree`/`lambda`/`pyz`, and `single` fails with an explanation.

---

## 7. Errors, tracebacks and the "source map" problem

### 7.1 Why Python needs less than JavaScript here

JS source maps exist because a bundler rewrites many files into one file and one coordinate space. Python tracebacks do not read the text of the file being run. They come from **code objects**, which carry their own `co_filename` and per-instruction `(line, end_line, col, end_col)` positions (PEP 626/657). If each module is compiled separately, with its original filename and original line numbers, the traceback is exact without any mapping step.

shakeout guarantees this:

- **Text formats.** Each kept statement is stored as a chunk tagged with its original start line. The runtime loader pads with newlines before `compile()`, so every line number is the original one. Removed code costs only the newline characters in the padding, generated at load time.
- **Minified/bytecode formats.** Compile directly from the AST, which retains the original positions. No text round-trip is needed.

Both were verified on CPython 3.14 during planning. A shaken module produced a traceback naming `app/handlers.py`, line 6, with the correct caret under `undefined_name`. A note added with `add_note` explained the removal.

### 7.2 Source lines when the original files are absent (Lambda)

Tracebacks show code lines via `linecache`. In Lambda the original files do not exist, so the loader registers each module's reconstructed source in `linecache` when it executes the module. As a result:

- `traceback`, `logging.exception`, the Lambda runtime's `stackTrace` field and Sentry's context lines all show real source;
- filenames are project-relative (`app/handlers.py`, `requests/models.py`) with an optional configurable prefix, so Sentry code mappings and IDE links resolve to the repo.

`--no-embed-source` saves the bytes. Tracebacks keep file and line but lose the code text, which `shakeout trace` can restore offline (§7.5).

### 7.3 Build manifest

Every artifact embeds a manifest; `lambda`/`tree` also write it as a sidecar JSON file. It records:
- build ID;
- git SHA and dirty flag;
- shakeout version, target Python and platform;
- per-module content hash and origin;
- the full **removed-symbol table** (`module:name → line span, reason`).

The build ID is attached as an exception note on unhandled errors in export wrappers, so a CloudWatch traceback says exactly which build produced it.

### 7.4 When removed code is reached

Removal happens only when the analysis is sure, but `getattr(mod, computed_name)` and plugin systems exist, so failures must be self-explanatory:

| Failure | Mechanism | Message |
|---|---|---|
| `from pkg.mod import removed` / `pkg.mod.removed` | Injected PEP 562 `__getattr__`, only if the module does not define its own, consulting the tombstone table | `ImportError: cannot import name 'removed' from 'pkg.mod' — removed by shakeout (unreferenced; defined at pkg/mod.py:40). Keep it with: --keep pkg.mod:removed` |
| `NameError` inside bundled code | Export/entry wrappers catch, consult the tombstones, `add_note`, re-raise unchanged | Original traceback plus a note naming the removed definition and the keep flag |
| Any of the above in development | `--debug` build: removed defs replaced by one-line stubs raising `RemovedByShakeout` with the full reason | The failing call site is in the traceback |

Notes never change the exception type, so `except ImportError:` fallbacks keep working.

### 7.5 Tooling

- `shakeout why pkg.mod:name` prints the reason chain that kept a definition (e.g. entry → `app/main.py@L3` → `pkg/__init__.py@L1` re-export → `pkg/core.py@L20`).
- `shakeout report [--json]` prints per-module statements and bytes kept or removed, dropped modules, warnings and escapes.
- `shakeout trace <traceback.txt> --manifest m.json` re-attaches source lines and removal explanations to a pasted traceback when source was not embedded. It also maps any format that does not preserve lines (reserved for future scope hoisting, so the manifest format includes a line map from day one).

---

## 8. Configuration and escape hatches (summary)

`keep` (symbol or module glob), `include`/`exclude` packages, `pure-calls`, `assume-pure-bases`, per-package `side-effects`, comment pragmas `# shakeout: pure|keep`, `--no-shake` (bundle without removal; isolates loader bugs from analysis bugs), `--debug`, `--minify`, `--no-embed-source`.

---

## 9. Test strategy

The model is a large-scale refactoring at Google. The test suite is written first, from this spec, and is the definition of done. Each bundler feature lands only when its pre-existing tests turn green. Every bug fix starts as a failing regression case.

Principles:
- **Hermetic and deterministic.** Each test runs in a temp dir, invokes Python with `-I`, sets fixed `PYTHONHASHSEED` and locale, and makes no network calls in tiers 1–4.
- **Oracles over assertions where possible.** Comparing against the unbundled original or CPython's own `symtable` catches bugs nobody thought to write a test for.
- **Two speed tiers.** `presubmit` must finish in under 60s on a laptop and runs on every change. `nightly` covers the corpus, fuzzing, the version matrix and Lambda.
- **Readable failures.** A failed case prints a unified diff of kept/removed symbols and the reason chain for the offending statement.

### 9.1 Tier 1: unit tests (per component)

- **Scope analysis, differential against `symtable`.** For every function in every `.py` file of the stdlib and the corpus, the set of names shakeout considers global references must equal what `symtable` reports as global/free-resolving-to-module. That is roughly 100k functions, it needs no hand-written expectations, and it catches comprehension, `nonlocal`, class-scope and walrus edge cases.
- **Effects classifier.** Table-driven, one row per rule in §4.3, with a pure and an impure example each, including the subtle ones: decorator calls with arguments, `field()` in dataclasses, `Generic[T]` bases, `metaclass=`, `__init_subclass__` inherited two levels away, AugAssign, attribute stores.
- **Resolver.** Relative imports at every level, namespace packages, `import a.b as c`, `from . import x` (submodule vs attribute), shadowed stdlib names, missing modules, CRLF and BOM sources, encoding cookies.
- **Linker.** Re-export chains, cycles, star imports, `__getattr__` modules, submodule-as-attribute after `import pkg.sub`.
- **Shaker.** Reachability on hand-built IR graphs, including cycles and roots in unloaded modules.
- **Runtime loader.** Tested standalone: specs, `__path__`, `__file__`, `importlib.reload`, `pkgutil`, linecache registration, pickling of objects from bundled modules.
- **Chunk emitter.** Line padding is exact. A property test generates random kept/removed subsets and asserts each kept statement's lines map to the original.

A small DSL keeps these tests readable:

```python
proj = project(
    {
        "main.py": "from lib import used\nused()",
        "lib/__init__.py": "from .core import used, unused",
        "lib/core.py": "def used(): print('hi')\ndef unused(): ...",
    }
)
result = shake(proj, entry="main.py")
assert result.removed == {"lib.core:unused"}
assert result.kept_reason("lib.core:used").chain == ["main.py@L1", "lib/__init__.py@L1"]
```

### 9.2 Tier 2: case catalog (golden fixtures)

`tests/cases/<category>/<case>/` contains `src/`, a `case.toml` (entry, options, expected removed/kept symbols, expected dropped modules, expected stdout/exit code, expected warnings), and a reviewed golden of the emitted bundle. `pytest --update-goldens` rewrites goldens. Golden diffs are code-reviewed like source.

Catalog, with at least 150 cases before Phase 2 starts:

- **Imports:** absolute, relative (levels 1–3), aliases, dotted, submodule-attribute, function-local, conditional/`try` fallbacks, `TYPE_CHECKING`, circular (both orders), star with and without `__all__`, re-export through 3 packages, namespace packages, the entry imported under its own name.
- **Definitions:** unused function, class, constant, overloads, properties, nested functions, closures over module globals, default arguments referencing globals, annotations referencing globals (with and without `from __future__ import annotations`, and 3.14 lazy annotations).
- **Effects:** registration decorators, `__init_subclass__` registries, metaclass registries, module-level mutation of another module (`other.X = 1`), `atexit`, signal handlers, logging config, `warnings.filterwarnings`, import-time prints, monkeypatching.
- **Dynamic:** `getattr(mod, name)`, `globals()[…]`, `importlib.import_module` with constant and computed names, `__import__`, `sys.modules[__name__]`, module `__getattr__`, `eval`/`exec`, plugin discovery via entry points.
- **Packages:** `__init__` side effects ordering, `__path__` manipulation, `pkgutil.extend_path`, data files via `__file__` and `importlib.resources`, `dist-info` metadata lookups.
- **Syntax by version:** `match`, PEP 695 generics and `type` aliases, `except*`, walrus everywhere, f-string nesting (3.12), t-strings (3.14).
- **Errors (§7):** reaching removed code via each mechanism, tracebacks through 3 bundled modules, chained exceptions, `ExceptionGroup`, syntax error in a bundled module, a missing external dependency at runtime.

### 9.3 Tier 3: behavioral equivalence

Every case and corpus driver runs twice, as the original and as the bundle, in clean subprocesses. The runs must match on:
- stdout and exit code exactly;
- stderr after normalizing paths;
- **traceback frames:** `(filename, line, function, source line)` per frame must be identical, which turns §7's promise into a hard gate;
- the set of modules in `sys.modules` at exit, restricted to bundled ones. Fewer is allowed only if the missing module was proven effect-free.

Each case also runs with `--no-shake`. If that fails, the bug is in the loader or emitter, not the analysis.

### 9.4 Tier 4: properties and invariants

- **Determinism.** The same input gives byte-identical output across runs, `PYTHONHASHSEED` values, filesystem ordering and platforms.
- **Idempotence.** Shaking a `tree` output again removes nothing more.
- **Monotonicity.** Adding a `keep` or an entry never removes a statement that was kept before.
- **Subset.** The kept set with `--assume-pure-bases` is a subset of the kept set without it.

### 9.5 Tier 5: soundness oracle (dynamic coverage ⊆ static kept set)

Run the *original* program, or its test suite, under `sys.monitoring` (3.12+) and record every top-level statement executed and every function or class body entered, mapped back to statement IDs. **Any executed statement that shakeout removed is a soundness bug,** even if outputs happened to match. This is the strongest single check in the suite. It is applied to every case, every corpus package's own test suite, and every fuzz program.

### 9.6 Tier 6: real-world corpus

A pinned, version-locked list of pure-Python packages in a committed `uv.lock`: click, attrs, rich, requests (+ urllib3, idna, certifi, charset-normalizer), jinja2 (+ markupsafe pure fallback), httpx (+ anyio, sniffio, h11), typer, packaging, python-dateutil, six, tomli, pyyaml (pure mode), structlog, boto3/botocore for Lambda relevance and size.

Per package:
1. Build a `tree` with the package included and shaken, entered through a driver script that exercises its main API. Run tiers 3 and 5.
2. **Run the package's own test suite against the shaken tree,** with entries set to its public API plus `keep` overrides recorded in a per-package manifest. Every keep override is justified in a comment and tracked. The suite passing is the gate.
3. Record metrics: bytes and modules before and after, kept ratio, analysis time, cold-import time. A dashboard JSON is stored per nightly so regressions in size or soundness are visible.

### 9.7 Tier 7: generative fuzzing

A Hypothesis strategy generates multi-module projects from a grammar of constructs (defs, classes with registries, decorators, re-exports, cycles, local imports, dynamic access with known keys) in which every program prints a trace of what ran. Oracles come from tiers 3 and 5. Failures are shrunk and written out automatically as new `tests/cases/fuzz/<hash>/` fixtures, so every fuzz find becomes a permanent regression test.

### 9.8 Tier 8: robustness and performance

- Analyze every `.py` in the stdlib and the corpus site-packages with no crashes and a per-file time budget. Files that fail to parse produce a diagnostic, not a crash.
- Benchmarks: analysis throughput (files/s), end-to-end build of botocore, peak memory. They are tracked over time, with a CI threshold on regressions.

### 9.9 Tier 9: integration

- **AWS Lambda.** Use the official `public.ecr.aws/lambda/python:3.13` image with the Runtime Interface Emulator. Build a `lambda` target, invoke the success path, then invoke a failing path and assert the returned `stackTrace` frames carry original filenames, lines and source text, plus the build-ID note. Requires Docker, so it runs nightly.
- **uv.** Build from a real `uv.lock` with `--python-platform` cross-targeting, and check the staged environment matches the lock.
- **Self-hosting.** shakeout bundles itself, and its own presubmit suite passes using the bundled build.

### 9.10 Meta-quality

- Mutation testing (mutmut) on `analysis/effects.py`, `graph/link.py` and `graph/shake.py`, targeting a ≥90% kill rate. A surviving mutant in the effects rules means a missing test.
- Branch coverage ≥95% on `analysis/`, `graph/` and `emit/runtime/`. Coverage alone is not a goal, only a floor.
- ruff and pyright strict, enforced in presubmit.
- Matrix: CPython 3.10–3.15, free-threaded 3.14t, Linux/macOS/Windows (path separators, CRLF, case-insensitive filesystems).

---

## 10. Delivery phases

Each phase has a written exit criterion. Nothing moves forward while an earlier gate is red.

| Phase | Scope | Exit criterion |
|---|---|---|
| **0. Test foundation** | Harness, project DSL, case format + runner, golden tooling, equivalence runner, `sys.monitoring` soundness oracle, `symtable` differential driver, corpus lockfile and drivers, CI with presubmit/nightly split. Write the full case catalog (§9.2) with expectations; all marked `xfail(strict=True)` | ≥150 cases written and reviewed; oracles validated against hand-made "broken bundler" stubs (a bundler that drops everything must fail every tier) |
| **1. Bundler without shaking** | Resolver, runtime loader, chunk emitter, `single`/`tree` targets, line preservation, linecache registration, manifest | All `--no-shake` equivalence and traceback-frame tests green on the full matrix |
| **2. Analysis** | Scopes, bindings, effects, dynamic detection | `symtable` differential at 100% on stdlib + corpus; effects table green |
| **3. Shaking** | Linker, reachability, module- then statement-level removal, reason graph | Case catalog green; soundness oracle clean on all cases; first-party corpus drivers green |
| **4. Diagnostics** | Tombstones, `__getattr__` injection, wrappers, `--debug` stubs, `why`, `report`, `trace` | All error-category cases green; messages reviewed for wording |
| **5. Dependencies + uv + Lambda** | uv staging, `include`, per-package config, `pyz`/`lambda` targets, data files, `dist-info` | Corpus test suites green against shaken trees; Lambda RIE test green |
| **6. Hardening** | Fuzzer in nightly, mutation testing, benchmarks, self-hosting, docs | Mutation ≥90%; a fuzz campaign of one week with no new finds; v0.1 release |
| **7. Later** | `--minify`/bytecode, multi-entry shared layers (Lambda Layers), `hatch-shakeout`, GitHub Action | Separate design notes |

---

## 11. Risks and open questions

- **Import-order changes.** Dropping an effect-free module can change the order other modules initialize in, which matters only for order-dependent circular imports. Mitigation: the equivalence tier checks `sys.modules` order on cases with cycles; there is an option to keep import order for cycles.
- **Plugin ecosystems** (pytest plugins, Django apps, SQLAlchemy declarative registries) are reached through metadata, settings strings or metaclasses the analysis cannot see. Mitigation: they are classified impure by the class-registration rules, and there is a documented `keep` recipe per framework in the corpus.
- **Savings may be modest for first-party code.** The large wins are in dependencies (botocore, rich), which is also the riskiest area. Phase 5 metrics decide how hard to push.
- **Free-threaded and future CPython** may change import internals. The matrix catches this early.
- **Open:** should the default target for Lambda be `tree`, which needs no loader and gives the simplest tracebacks, or `single`, which gives the smallest cold-start file count? Proposal: `tree`, decided by cold-start benchmarks in Phase 5.
- **Open:** final name.

---

## 12. Sources

- pyodide-pack: https://github.com/pyodide/pyodide-pack
- "Package Pruning", discuss.python.org (2024): https://discuss.python.org/t/package-pruning/59257
- "Bundling Python apps for the web": https://discuss.python.org/t/bundling-python-apps-for-the-web-top-level-imports/15663
- uv releases: https://github.com/astral-sh/uv/releases
- uv build/metadata hook request: https://github.com/astral-sh/uv/issues/14561
- uv_build limitations: https://pydevtools.com/handbook/reference/uv-build/
- PEP 626 (precise line numbers), PEP 657 (fine-grained error locations), PEP 562 (module `__getattr__`), PEP 678 (exception notes)
