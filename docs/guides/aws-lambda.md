# AWS Lambda

Lambda imports your handler module by name and calls a function in it. Tell shakeout which
function that is with `--export`. The entry is then analyzed as an importable module, so its
`if __name__ == "__main__":` block is dropped.

```python title="handler.py"
import json

from service import greet


def handler(event, context):
    return {"statusCode": 200, "body": json.dumps(greet(event["name"]))}
```

```console
$ shakeout build handler.py -t lambda --export handler -o dist
wrote dist/handler.zip (lambda, build 0c3dbd0ba4cc): 2 modules, 4/5 top-level statements kept, 0 modules dropped
handler: handler.handler
```

Upload `dist/handler.zip` and set the function's handler to the printed value
(`handler.handler`). The unzipped package is also left in `dist/package/` for inspection.

```toml title="pyproject.toml"
[tool.shakeout]
entry = "src/handler.py"
target = "lambda"
exports = ["handler"]
uv = true                          # install locked dependencies...
python-version = "3.13"            # ...for the Lambda runtime
platform = "x86_64-manylinux_2_28" # ...and architecture
```

## What goes into the zip

- Your modules, shaken, at their import paths and with original line numbers.
- Third-party distributions you don't `include`, copied whole: compiled extensions, data files
  and `.dist-info` metadata. See [Dependencies and uv](dependencies.md).
- `shakeout-manifest.json`, with the build ID, modules, removals and handlers.

The zip is reproducible: building the same sources twice gives byte-identical archives (fixed
timestamps and order), so its hash is a usable cache key.

## Errors in production

Lambda reports errors with frames taken from Python's traceback machinery. Because bundled files
keep their names and line numbers, the `stackTrace` in the error response and in CloudWatch
points at `service.py`, line 2, just as it would locally, and shows the source line. No mapping
step is involved. See [Debugging tracebacks](tracebacks.md).

!!! note "Handlers in other modules"
    `--export` takes names in the entry module, or `module:name` for anything else that outside
    code calls.
