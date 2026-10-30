# Your first bundle

Take a small program with a helper module:

```python title="main.py"
from textutil import shout

print(shout("hello"))
```

```python title="textutil.py"
import json


def shout(text):
    return text.upper() + "!"


def whisper(text):  # never used
    return text.lower()


def to_json(obj):  # never used
    return json.dumps(obj)
```

## Build it

```console
$ shakeout build main.py -o dist
wrote dist/main.py (single, build f723619611b7): 2 modules, 3/6 top-level statements kept, 0 modules dropped
$ python dist/main.py
HELLO!
```

`dist/main.py` is self-contained: it runs on a machine that only has Python. `whisper`, `to_json`
and the unused `import json` are gone.

## See why

```console
$ shakeout report main.py
module __main__ [main.py] bundled
  + L1        from textutil import shout  [used as shout]
  + L3        print(shout("hello"))  [root: expr statement at module level]
module textutil [textutil.py] bundled
  - L1        import json
  + L4-5      def shout(text):  [used as shout]
  - L8-9      def whisper(text):  # never used
  - L12-13    def to_json(obj):  # never used
external: json
```

Every top-level statement is listed with `+` (kept), `-` (removed) or `~` (kept, but its import
was [rewritten](../concepts/import-rewriting.md)), plus the reason it was kept. To follow one
symbol back to the code that keeps it:

```console
$ shakeout why main.py textutil:shout
textutil@L4-L5 (ref: shout)
  <- __main__@L3-L3 (root: expr statement at module level)
```

## Configure it

Put the options in `pyproject.toml` so `shakeout build` needs no arguments:

```toml title="pyproject.toml"
[tool.shakeout]
entry = "main.py"
target = "single"
out = "dist"
```

## Next steps

- [AWS Lambda](../guides/aws-lambda.md): build a deployment package.
- [Dynamic code](../guides/dynamic-code.md): what to do when code is reached by name.
- [How shaking works](../concepts/how-it-works.md).
