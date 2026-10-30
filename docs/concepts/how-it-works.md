# How shaking works

```text
 resolve ─► parse ─► analyze ─► link ─► shake ─► emit
 modules     AST     scopes,     cross-   reach-   chunks with
 (no exec)           effects     module   ability  original lines
```

1. **Resolve.** Starting from the entry script, shakeout follows every `import`, including ones
   inside functions, and locates modules the way Python would, without executing anything.
   Modules under your source roots are bundled. The standard library and other packages are
   external.
2. **Parse and analyze.** Each module is split into top-level statements. For each one,
   shakeout records the names it binds, the names it reads, using exact Python scoping rules,
   the modules it may import, and whether running it has [side effects](side-effects.md).
3. **Link.** References like `pkg.sub.func` are followed through imports and re-exports to the
   statement that defines them.
4. **Shake.** Starting from the entry point, every statement with side effects in an imported
   module, and every export or keep, is marked live, then everything live code references.
   A module that nothing live imports is dropped entirely.
5. **Emit.** Kept statements are written with their original line numbers, and kept imports are
   [rewritten](import-rewriting.md) to drop unused names. Removed names get a small
   `__getattr__` that explains them if they are reached at runtime.

## Per-module isolation

Unlike JavaScript bundlers, shakeout doesn't merge modules into one namespace ("scope
hoisting"). Every module keeps its own globals, `__name__`, import order and circular-import
behavior. That costs a little size and buys exact semantics and honest tracebacks.

## What is shaken

The unit of removal is a top-level statement: a function, class, assignment or import.
Methods inside a kept class stay. Whether an instance's method is called can't be decided
statically.
