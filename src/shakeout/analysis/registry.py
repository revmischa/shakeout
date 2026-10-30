"""Static knowledge used by the effects rules.

Known-pure callables, safe base classes, and which expressions are pure when their parts are.
Pure data built once at import, so it is kept out of mutation testing (mutants of import-time
code can never be observed).
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, cast

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
PURE_IF_CHILDREN_PURE: dict[type[ast.AST], Callable[[ast.AST], list[ast.expr | None]]] = {
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
