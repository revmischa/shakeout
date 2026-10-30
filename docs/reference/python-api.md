# Python API

`shakeout.api` is the stable surface the CLI is built on.

```python
from shakeout.api import Options, analyze, build

result = analyze("app/main.py", Options(keep=("app.hooks:on_load",)))
print(sorted(result.removed))  # {"module:name", ...}
print(result.why("app.core:helper"))  # reason chain, innermost first

built = build("handler.py", "dist", target="lambda", options=Options(exports=("handler",)))
print(built.run_path, built.manifest["build_id"])
```

| Name | Description |
|---|---|
| `analyze(entry, options=None) -> ShakeResult` | Analyze without writing anything |
| `build(entry, out, *, target="single", options=None) -> BuildResult` | Analyze and write a target into the directory `out` |
| `Options` | Frozen dataclass mirroring the [configuration](configuration.md) keys, in snake_case |
| `ShakeResult` | `kept`, `removed`, `bundled_modules`, `dropped_modules`, `diagnostics`, `statements`, `why(symbol)` |
| `BuildResult` | `run_path`, `out`, `manifest`, `result`, `runtime_files` |
| `render_report(result) -> str` | The text that `shakeout report` prints |
| `TARGETS` | `("single", "tree", "pyz", "lambda")` |
| `BuildError`, `ArchiveError` | Raised for unbuildable input and for a target that doesn't fit the entry |
