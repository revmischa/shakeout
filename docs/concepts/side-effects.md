# Side effects

Importing a Python module runs it. A statement may only be removed if running it does nothing
observable, which means it only creates bindings in its own module, and if nothing reachable uses
those bindings.

## Removable when unused

| Statement | Condition |
|---|---|
| `def` / `async def` | No decorators, or only known-pure ones (`dataclass`, `lru_cache`, `property`, `contextmanager`, ...), and pure default values |
| `class` | Pure decorators, no class keywords, a pure body, and bases that don't register subclasses |
| `x = ...` | Plain name targets and a pure value: literals, names, operators, lambdas, and calls to known-pure constructors like `TypeVar`, `namedtuple`, `re.compile`, `frozenset`, `field` |
| `import` | Every module it loads is side-effect free: the standard library, or bundled modules with no side effects |
| `if TYPE_CHECKING:` | Always |

## Always kept

Calls at module level, loops, `try`, `with`, attribute or item stores (`config.X = 1`), augmented
assignment, `del`, `__future__` imports, module docstrings, dunders like `__all__` and
`__getattr__`, and anything marked `# shakeout: keep`.

## Classes that register

Defining a subclass runs `__init_subclass__` and metaclass code, which is how plugin registries
work. A class is *registering* if it defines `__init_subclass__`, has a metaclass, or inherits from
a registering class. Subclasses of registering classes are always kept. Bases imported from
packages outside the bundle are assumed to register too, unless they are known to be safe
(`Exception`, `Enum`, `Protocol`, `NamedTuple`, `ABC`, the `collections.abc` types, ...).
`assume-pure-bases = true` trusts all unknown bases.

## Effects travel through imports

A module has side effects if it runs a side-effecting statement or imports a module that has
them. An `import` of such a module is kept even when its names are unused, because removing it
would skip those effects.

## Assumptions

These are documented so you can judge them:

- Operators, attribute reads and subscripts on module-level values don't call user code with
  side effects.
- The known-pure callables don't call back into user code. Builtins that do, like `sorted(...,
  key=f)` and `map`, are not on the list.
- Importing standard-library modules has no observable effects, except for a short denylist
  such as `this`, `antigravity` and `turtle`.
