# Dynamic code

Static analysis sees `import x`, `x.attr` and `from x import y`. It can't see names computed at
runtime. shakeout handles the common patterns automatically and asks for help with the rest.

## Handled automatically

| Pattern | What happens |
|---|---|
| Module calls `globals()`, `vars()`, `locals()`, `eval`, `exec` or touches `sys.modules` | The module is kept whole |
| A module object is passed around (`getattr(mod, name)`, `f(mod)`) | That module is kept whole |
| `from x import *` | `x` is kept whole |
| `importlib.import_module("pkg.sub")` with a constant | Followed like an import |
| `importlib.import_module("plugins." + name)` | Every module under `plugins` is bundled, with a warning |
| Registration decorators, `__init_subclass__`, metaclasses | The registered classes and functions are kept. See [Side effects](../concepts/side-effects.md) |

## Needs a `keep`

A fully computed module name, plugins listed in settings files or entry points, and code
generated from strings need to be declared:

```toml
[tool.shakeout]
keep = [
  "myapp.plugins.csv",        # a module, kept whole
  "myapp.hooks:on_startup",   # one definition
]
```

Or in the source, next to the definition:

```python
def on_startup():  # shakeout: keep
    ...
```

The build warns about every dynamic import it can't resolve, and the runtime notes name the
exact `keep` entry to add. You don't have to guess.

## The opposite: telling shakeout a call is harmless

Calls to unknown functions are assumed to have side effects. When you know better:

```python
DEFAULTS = build_defaults()  # shakeout: pure
```

or, for every call site, `pure-calls = ["myapp.config.build_defaults"]`.
