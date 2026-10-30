# Import rewriting

A kept `from m import a, b` fails at runtime if `b` no longer exists. Keeping `b` just for
that would keep most of a library's public API alive. Instead, shakeout rewrites kept imports,
like esbuild does:

```python
from lib import used, unused  # before
from lib import used  # shakeout: was 'from lib import used, unused'   # after
```

If none of the names are used but the import is kept for its side effects, it becomes a plain
import:

```python
import lib as _shakeout_import_1  # shakeout: was 'from lib import a, b'
```

The rewritten line occupies the same number of lines as the original, and it carries a comment
saying what it was, so a traceback through it explains itself.

## Not rewritten

- Imports of modules outside the bundle: a missing name must still raise `ImportError`.
- Imports in modules kept whole (for example because they call `globals()`).
- Plain `import x`. It never requires attributes to exist, so only names of side-effect-free
  bundled modules are dropped, and traceback text stays identical.
- A `from pkg import sub` where `sub` is a submodule with side effects.
