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

## Where third-party packages come from

shakeout looks for third-party packages in the project's environment, never wherever shakeout
itself happens to be installed. In order:

1. `--search-path` directories and the `--uv` staging directory.
2. An activated virtual environment (`$VIRTUAL_ENV`), unless it's the one running shakeout.
3. A `.venv` next to the project's `pyproject.toml`, or next to the entry script if there's no
   `pyproject.toml`.
4. The interpreter running shakeout, but only if its environment is inside the project, as with
   `uv run shakeout` in a project that depends on shakeout.

An optional import of a package that isn't in the project's environment stays an optional
import, the same as in production.

## Data files in your own packages

Non-Python files inside your packages are copied into `tree` and `lambda` outputs. Examples are
a vendored `certifi/cacert.pem` and `templates/` directories. `single` and `pyz` bundles can't
serve files through `__file__` paths, so the build warns when bundled code uses `__file__`.

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
