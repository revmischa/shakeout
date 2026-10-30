# Targets

| Target | Output | Runs as | Contains third-party code |
|---|---|---|---|
| `single` | `<entry>.py` (+ `<entry>.manifest.json`) | `python main.py`, or `import` it with `--export` | Only `include`d pure-Python packages |
| `pyz` | `<entry>.pyz` | `python main.pyz` or `./main.pyz` | Yes, vendored |
| `tree` | a directory, one file per module | `python dist/main.py` | Yes, vendored |
| `lambda` | `<entry>.zip` and `package/` | AWS Lambda handler `<entry>.<export>` | Yes, vendored |

## single

A small runtime (about 200 lines) installs an in-memory importer and runs the entry in the real
`__main__` namespace. The program sees exactly the globals it would see unbundled, and `pickle`
of classes defined in the entry works. Each module is stored as chunks tagged with their original
start line and padded back to the original line numbers when compiled.

## tree

Every module is written to its import path with removed statements replaced by blank lines. No
runtime is involved: tracebacks name real files.

## pyz

A `tree` packed into a reproducible zipapp. Its `__main__.py` registers archived sources with
`linecache` (CPython's zipimport only does this lazily for tracebacks and splits lines
incorrectly when files contain form feeds) and runs the entry with `runpy`.

## lambda

A `tree` with vendored dependencies, zipped reproducibly (fixed timestamps, sorted entries). It
requires `exports`, and the manifest lists the handler names.
