---
hide:
  - navigation
---

<div class="hero" markdown>

# shakeout

<p class="tagline">A tree-shaking bundler for Python. It finds the code your program can actually reach, removes the rest, and ships one file, a zipapp, a pruned tree or an AWS Lambda package. Tracebacks still point at your real files and lines.</p>

[Get started](getting-started/index.md){ .md-button .md-button--primary }
[How it works](concepts/how-it-works.md){ .md-button }

</div>

```console
$ uvx shakeout build app/main.py -o dist
wrote dist/main.py (single, build 3f9c0a1d2e4b): 24 modules, 693/764 top-level statements kept, 1 modules dropped
$ python dist/main.py
```

<div class="grid" markdown>

<div class="card" markdown>
### Symbol-level shaking
Removes unused functions, classes and constants across module boundaries, not just whole modules.
</div>

<div class="card" markdown>
### Sound by default
Anything the analysis can't prove unused stays: registries, metaclasses, `globals()`, module objects passed around.
</div>

<div class="card" markdown>
### Exact tracebacks
Original file names, line numbers, carets and source lines, even in Lambda. No source maps needed.
</div>

<div class="card" markdown>
### Self-explaining failures
If something removed is reached at runtime, the error says what was removed and the setting that keeps it.
</div>

<div class="card" markdown>
### Built for deploys
Single file, zipapp, pruned tree or a reproducible Lambda zip. Locked dependencies come from `uv.lock`.
</div>

<div class="card" markdown>
### Tested like a compiler
A soundness oracle, CPython `symtable` cross-checks, fuzzing, mutation testing and real-library corpora.
</div>

</div>

## Why

JavaScript bundlers have removed dead code for years. Python deployments ship everything that is
installed, which hurts most where size and cold starts matter: serverless functions, CLI tools
and containers. Python's import-time side effects and dynamic features make this harder than in
JavaScript, so shakeout treats soundness as the first requirement and checks it with tests that
watch real programs run.

## At a glance

| You want to | Run |
|---|---|
| Bundle a script into one runnable file | `shakeout build app.py -o dist` |
| Build an AWS Lambda package | `shakeout build handler.py -t lambda --export handler` |
| See what is kept and why | `shakeout report app.py`, `shakeout why app.py pkg.mod:name` |
| Restore original lines in a traceback | `shakeout trace tb.txt --manifest dist/main.manifest.json` |

shakeout supports CPython 3.10 through 3.15. Its only runtime dependency is `tomli`, on Python 3.10.
