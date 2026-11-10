# Diagnostics

## Build warnings

| Message | Meaning and fix |
|---|---|
| `dynamic import at FILE:LINE: the module name is computed at runtime; list possible modules in the keep option` | `import_module(x)` with a computed name. Add the modules to `keep` |
| `dynamic import at FILE:LINE: bundling every module under 'PKG'` | `import_module("PKG." + x)`: every submodule of `PKG` is bundled |
| `FILE:LINE uses __file__; package data files ship with the tree and lambda targets, ...` | Files next to modules are copied into `tree` and `lambda` outputs. `single` and `pyz` bundles can't read them |
| `keep option 'SPEC': module 'M' was not found in the roots` | Typo or module outside the roots |
| `keep option 'SPEC': nothing in the bundle defines 'NAME'` | The module exists but defines no such name |
| `export 'NAME': nothing in the bundle defines 'NAME'` | The handler name is wrong |

`report` also lists `info: kept MOD whole: REASON` for modules kept whole because they use
`globals()`, `eval` or similar.

## Runtime notes

Added to exceptions raised in bundles. The exception type is unchanged.

| Exception | Note |
|---|---|
| `AttributeError` on a module | `removed by shakeout (unreferenced; defined at FILE:LINE). Keep it with: keep = ['MOD:NAME']` |
| `ImportError: cannot import name` | `'NAME' was removed from FILE:LINE ... Keep it with: keep = ['MOD:NAME']` |
| `NameError` | `'NAME' was removed by shakeout from FILE:LINE (unreferenced). Keep it with: ...` |
| `ModuleNotFoundError` | The module was dropped, or a dynamic import the build warned about. Names the `keep` entry |
