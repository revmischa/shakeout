# Dependencies and uv

By default shakeout bundles and shakes your project's own code, meaning modules under the entry
script's directory and any extra `roots`. The standard library is never bundled. Third-party
packages are handled one of three ways:

| Mode | How | Result |
|---|---|---|
| Vendored (default for `tree`, `pyz`, `lambda`) | nothing to configure | Each imported distribution is copied whole, with data files and metadata |
| Shaken | `include = ["requests", "idna"]`, or `["*"]` for all | Pure-Python packages are analyzed and shaken like your code. Their `.dist-info` is kept |
| External | `copy-dependencies = false` | Nothing is shipped. The runtime environment must provide them |

`single` bundles can only contain source, so third-party code has to be `include`d there.

## Locked dependencies with uv

```console
$ shakeout build --uv --python-version 3.13 --platform x86_64-manylinux_2_28
```

With `--uv` (or `uv = true`), shakeout runs `uv export --frozen --no-dev` to read the exact pins
from `uv.lock`, then `uv pip install --target` to install them for the target interpreter and
platform. The result is cached under `.shakeout/staging/`, keyed by the lockfile contents and
the target. shakeout never resolves versions itself.

## When shaking a dependency breaks it

Libraries sometimes reach their own code in ways static analysis can't see. Jinja2's template
compiler, for example, generates source that imports names from `jinja2.runtime` by string. The
failure is explicit:

```text
ImportError: cannot import name 'TemplateReference' from 'jinja2.runtime' (jinja2/runtime.py)
shakeout: 'TemplateReference' was removed from jinja2/runtime.py:... because nothing references it
statically (it may be imported by generated code). Keep it with: keep = ['jinja2.runtime:TemplateReference']
```

Keeping the whole module is usually the right fix: `keep = ["jinja2.runtime"]`.
