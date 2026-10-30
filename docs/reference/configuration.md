# Configuration

shakeout reads `[tool.shakeout]` from the nearest `pyproject.toml` above the entry script, or the
file given with `--config`. Paths are relative to that file. Unknown keys and wrong types are
errors.

```toml
[tool.shakeout]
entry = "src/app/main.py"
target = "lambda"
out = "dist"
exports = ["handler"]
keep = ["app.plugins.csv", "app.hooks:on_startup"]
include = ["attrs"]
uv = true
python-version = "3.13"
platform = "x86_64-manylinux_2_28"
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `entry` | string | none | Entry script |
| `target` | string | `"single"` | `single`, `tree`, `pyz` or `lambda` |
| `out` | string | `"dist"` | Output directory |
| `keep` | list | `[]` | Modules (`pkg.mod`, kept whole) or definitions (`pkg.mod:name`) to keep |
| `include` | list | `[]` | Third-party packages to bundle and shake. `"*"` means all |
| `exports` | list | `[]` | Entry functions called from outside, which makes the entry a library module |
| `pure-calls` | list | `[]` | Qualified callables whose calls have no side effects |
| `assume-pure-bases` | bool | `false` | Trust unknown base classes not to register subclasses |
| `roots` | list | `[]` | Extra first-party source roots |
| `search-path` | list | `[]` | Extra directories with third-party packages |
| `tombstones` | bool | `true` | Explain removed names when they are reached at runtime |
| `embed-source` | bool | `true` | Show source lines in tracebacks from the bundle |
| `minify` | bool | `false` | Drop comments and blank lines. Line numbers stay exact |
| `strip-docstrings` | bool | `false` | Drop docstrings (changes `__doc__`) |
| `copy-dependencies` | bool | `true` | Ship unshaken third-party distributions in tree-shaped targets |
| `uv` | bool | `false` | Install locked dependencies from `uv.lock` |
| `python-version` | string | the current interpreter | Target Python for `uv` |
| `platform` | string | the current platform | Target platform for `uv` |
