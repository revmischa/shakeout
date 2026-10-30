"""Side-effect classification of top-level statements.

A statement is *pure* when executing it only creates bindings in its own module: it cannot print,
mutate shared state, register anything, or fail in a way that matters. Pure statements may be
removed when nothing references what they bind. Everything else is impure and always kept.

The rules are deliberately conservative. Known assumptions (documented in docs/PLAN.md):
operators, attribute reads and subscripts on module-level values do not call user code with side
effects, and the listed pure callables do not call back into user code.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, cast

from shakeout.model import ImportTarget, ModuleTarget

if TYPE_CHECKING:
    from collections.abc import Callable

_TYPING = ("typing", "typing_extensions")


def _qualified(*names: str) -> frozenset[str]:
    return frozenset(names)


PURE_CALLABLES = _qualified(
    # builtins that construct values and never call user callbacks
    *(
        f"builtins.{n}"
        for n in (
            "object",
            "int",
            "float",
            "complex",
            "str",
            "bytes",
            "bytearray",
            "bool",
            "tuple",
            "list",
            "dict",
            "set",
            "frozenset",
            "range",
            "slice",
            "len",
            "repr",
            "ord",
            "chr",
            "hex",
            "oct",
            "bin",
            "abs",
            "round",
            "property",
            "staticmethod",
            "classmethod",
            "isinstance",
            "issubclass",
            "callable",
            "id",
            "memoryview",
        )
    ),
    *(
        f"{m}.{n}"
        for m in _TYPING
        for n in (
            "TypeVar",
            "NewType",
            "ParamSpec",
            "TypeVarTuple",
            "NamedTuple",
            "TypedDict",
            "cast",
            "overload",
            "final",
            "runtime_checkable",
            "no_type_check",
            "dataclass_transform",
            "override",
            "TypeAliasType",
            "get_type_hints",
            "deprecated",
        )
    ),
    "collections.namedtuple",
    "collections.OrderedDict",
    "collections.defaultdict",
    "collections.deque",
    "collections.Counter",
    "collections.ChainMap",
    "re.compile",
    "dataclasses.field",
    "dataclasses.dataclass",
    "dataclasses.make_dataclass",
    "functools.partial",
    "functools.lru_cache",
    "functools.cache",
    "functools.wraps",
    "functools.cached_property",
    "functools.total_ordering",
    "functools.singledispatch",
    "functools.singledispatchmethod",
    "functools.partialmethod",
    "contextlib.contextmanager",
    "contextlib.asynccontextmanager",
    "abc.abstractmethod",
    "abc.abstractproperty",
    "abc.abstractclassmethod",
    "abc.abstractstaticmethod",
    "enum.auto",
    "enum.unique",
    "enum.verify",
    "enum.member",
    "enum.nonmember",
    "logging.getLogger",
    "operator.itemgetter",
    "operator.attrgetter",
    "operator.methodcaller",
    "pathlib.Path",
    "pathlib.PurePath",
    "pathlib.PurePosixPath",
    "pathlib.PureWindowsPath",
    "decimal.Decimal",
    "fractions.Fraction",
    "datetime.timedelta",
    "datetime.date",
    "datetime.datetime",
    "datetime.time",
    "datetime.timezone",
    "types.MappingProxyType",
    "types.SimpleNamespace",
    "struct.Struct",
    "string.Template",
    "threading.Lock",
    "threading.RLock",
    "threading.local",
    "threading.Event",
    "weakref.WeakValueDictionary",
    "weakref.WeakKeyDictionary",
    "weakref.WeakSet",
    "weakref.ref",
    "contextvars.ContextVar",
    "uuid.UUID",
    "ipaddress.ip_address",
    "ipaddress.ip_network",
)

# Bases whose subclassing has no global side effects.
PURE_BASES = _qualified(
    *(
        f"{m}.{n}"
        for m in _TYPING
        for n in (
            "NamedTuple",
            "TypedDict",
            "Protocol",
            "Generic",
            "Any",
        )
    ),
    "abc.ABC",
    "enum.Enum",
    "enum.IntEnum",
    "enum.Flag",
    "enum.IntFlag",
    "enum.StrEnum",
    "enum.ReprEnum",
    "collections.OrderedDict",
    "collections.defaultdict",
    "collections.UserDict",
    "collections.UserList",
    "collections.UserString",
    "collections.namedtuple",
    "threading.Thread",
    "threading.local",
    "json.JSONEncoder",
    "json.JSONDecoder",
    "argparse.Action",
    "argparse.ArgumentParser",
    "string.Formatter",
    "io.StringIO",
    "io.BytesIO",
    "contextlib.AbstractContextManager",
    "contextlib.ContextDecorator",
    "html.parser.HTMLParser",
)
PURE_BASE_MODULES = frozenset({"collections.abc", "numbers", "typing", "typing_extensions"})


class EffectRules:
    """Purity rules for one module. ``qualify`` maps an expression to a dotted external name."""

    def __init__(
        self,
        qualify: Callable[[ast.expr], str | None],
        extra_pure: frozenset[str] = frozenset(),
    ) -> None:
        self.qualify = qualify
        self.pure_callables = PURE_CALLABLES | extra_pure

    # -- expressions -------------------------------------------------------------------------
    def pure_callable(self, func: ast.expr) -> bool:
        q = self.qualify(func)
        return q is not None and q in self.pure_callables

    def pure_expr(self, node: ast.expr | None) -> bool:
        """Whether evaluating ``node`` at module level has no observable side effects."""
        if node is None or isinstance(node, (ast.Constant, ast.Name, ast.Lambda)):
            return True
        if isinstance(node, ast.Call):
            return (
                self.pure_callable(node.func)
                and all(self.pure_expr(a) for a in node.args)
                and all(self.pure_expr(k.value) for k in node.keywords)
            )
        children = _PURE_IF_CHILDREN_PURE.get(type(node))
        # Anything else (comprehensions may call user __iter__, walrus binds, await...) is impure.
        return children is not None and all(self.pure_expr(c) for c in children(node))

    def pure_decorator(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Call):
            return self.pure_expr(node)
        return self.pure_callable(node)

    # -- statements --------------------------------------------------------------------------
    def function_effect(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
        for deco in node.decorator_list:
            if not self.pure_decorator(deco):
                return f"decorator {ast.unparse(deco)!r} may have side effects"
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if not self.pure_expr(default):
                return "default argument value may have side effects"
        return None

    def class_shell_effect(self, node: ast.ClassDef) -> str | None:
        """Effects from decorators, keywords and body; bases are checked by the linker."""
        for deco in node.decorator_list:
            if not self.pure_decorator(deco):
                return f"class decorator {ast.unparse(deco)!r} may have side effects"
        for kw in node.keywords:
            return f"class keyword {kw.arg or '**'!s}= is passed to the metaclass/__init_subclass__"
        for base in node.bases:
            if isinstance(base, ast.Starred):
                return "starred class bases"
        for stmt in node.body:
            reason = self.class_body_effect(stmt)
            if reason:
                return reason
        return None

    def class_body_effect(self, stmt: ast.stmt) -> str | None:
        match stmt:
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                return self.function_effect(stmt)
            case ast.ClassDef():
                return self.class_shell_effect(stmt) or (
                    None if all(self.pure_expr(b) for b in stmt.bases) else "nested class base"
                )
            case ast.Assign(targets=targets, value=value):
                if all(isinstance(t, ast.Name) for t in targets) and self.pure_expr(value):
                    return None
                return "class attribute initializer may have side effects"
            case ast.AnnAssign(target=ast.Name(), value=value):
                return (
                    None
                    if self.pure_expr(value)
                    else "class attribute initializer may have side effects"
                )
            case ast.Expr(value=ast.Constant()) | ast.Pass():
                return None
            case _:
                return f"class body contains {type(stmt).__name__}"

    def assignment_effect(self, stmt: ast.Assign | ast.AnnAssign) -> str | None:
        if isinstance(stmt, ast.Assign):
            targets: list[ast.expr] = list(stmt.targets)
            value = stmt.value
        else:
            if stmt.value is None and isinstance(stmt.target, ast.Name):
                return None
            targets, value = [cast("ast.expr", stmt.target)], stmt.value
        for t in targets:
            for sub in ast.walk(t):
                if isinstance(sub, (ast.Attribute, ast.Subscript)):
                    return "assignment stores into an attribute or item"
        if not self.pure_expr(value):
            return f"initializer {_short(value)} may have side effects"
        return None


_SHORT = 40


def _short(node: ast.expr | None) -> str:
    if node is None:
        return "''"
    text = ast.unparse(node)
    return repr(text if len(text) <= _SHORT else text[: _SHORT - 3] + "...")


def is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
        sides = [test.left, test.comparators[0]]
        names = [s for s in sides if isinstance(s, ast.Name) and s.id == "__name__"]
        consts = [s for s in sides if isinstance(s, ast.Constant) and s.value == "__main__"]
        return bool(names and consts)
    return False


def is_type_checking_guard(node: ast.stmt, qualify: Callable[[ast.expr], str | None]) -> bool:
    if not isinstance(node, ast.If) or node.orelse:
        return False
    q = qualify(node.test)
    if q in {"typing.TYPE_CHECKING", "typing_extensions.TYPE_CHECKING"}:
        return True
    return isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"


def qualify_target(target: ImportTarget, attrs: tuple[str, ...]) -> str:
    """Dotted name of ``attrs`` reached through an import target."""
    if isinstance(target, ModuleTarget):
        return ".".join((target.module, *attrs))
    return ".".join((target.module, target.attr, *attrs))


def _children(*fields: str) -> Callable[[ast.AST], list[ast.expr | None]]:
    def get(node: ast.AST) -> list[ast.expr | None]:
        out: list[ast.expr | None] = []
        for name in fields:
            value: object = getattr(node, name)
            if isinstance(value, list):
                out.extend(cast("list[ast.expr | None]", value))
            else:
                out.append(cast("ast.expr | None", value))
        return out

    return get


# Expressions that are pure exactly when all of their sub-expressions are pure.
_PURE_IF_CHILDREN_PURE: dict[type[ast.AST], Callable[[ast.AST], list[ast.expr | None]]] = {
    ast.Attribute: _children("value"),
    ast.Subscript: _children("value", "slice"),
    ast.Tuple: _children("elts"),
    ast.List: _children("elts"),
    ast.Set: _children("elts"),
    ast.Starred: _children("value"),
    ast.Dict: _children("keys", "values"),
    ast.BinOp: _children("left", "right"),
    ast.UnaryOp: _children("operand"),
    ast.BoolOp: _children("values"),
    ast.Compare: _children("left", "comparators"),
    ast.IfExp: _children("test", "body", "orelse"),
    ast.JoinedStr: _children("values"),
    ast.FormattedValue: _children("value", "format_spec"),
    ast.Slice: _children("lower", "upper", "step"),
}
