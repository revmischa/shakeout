# Soundness and testing

**Sound** means: if the original program would run a piece of code, the bundle still contains it.
A bundler that removes too much breaks programs silently, so shakeout's test suite exists
mainly to make that impossible to miss.

| Check | What it catches |
|---|---|
| Case catalog (126 multi-file programs) | Expected removals, plus byte-identical stdout, exit codes and traceback frames for every target, with and without shaking and minification |
| Soundness oracle | Runs the *original* program under `sys.monitoring` and fails if any function that executed was removed, even if output happened to match |
| `symtable` differential | Scope analysis against CPython's own symbol table on every standard-library file |
| Minify equivalence | Minified standard-library sources compile to identical instructions on identical lines |
| Fuzzing | Hypothesis generates multi-module programs with registries, plugins, cycles, local imports and `globals()`, and checks equivalence and soundness |
| Real-world corpus | attrs, click, dateutil, idna, jinja2, packaging, rich, six and tomli, shaken and compared |
| Mutation testing | Mutants of the effects rules, class analysis and reachability engine must be caught. The current kill rate is 100% |
| Self-hosting | shakeout bundles itself and the bundle produces byte-identical output. Shaking shakeout from its API removes nothing |

Every bug these found became a permanent regression case. The list is in the
[design document](../project/design.md).

## Known limits

- Code reached only through strings (settings files, entry points, generated code) needs `keep`.
- Data files next to modules are only shipped in vendored distributions. Reading `__file__` in
  your own modules produces a warning.
- Dropping a side-effect-free module can change the order in which other modules initialize.
  This matters only for order-dependent circular imports.
