"""Targeted scope-analysis tests for constructs the symtable sweep may not exercise."""

from __future__ import annotations

import ast
import textwrap

import pytest

from shakeout.analysis.scopes import ImportContext, collect, module_bindings
from shakeout.model import FromTarget, ModuleTarget, NameTarget


def refs(source: str) -> set[str]:
    stmt = ast.parse(textwrap.dedent(source)).body[0]
    out = collect(stmt, ImportContext("pkg.mod", is_package=False))
    return {r.base.name for r in out.refs if isinstance(r.base, NameTarget)}


CASES: list[tuple[str, str, set[str]]] = [
    ("param shadows", "def f(x): return x", set()),
    ("local shadows", "def f():\n    y = 1\n    return y", set()),
    ("read before local assignment is still local", "def f():\n    print(y)\n    y = 1", {"print"}),
    ("global decl", "def f():\n    global y\n    y = 1\n    return y", {"y"}),
    (
        "global decl in nested beats enclosing local",
        "def f():\n    G = 1\n    def g():\n        global G\n        return G\n    return g",
        {"G"},
    ),
    (
        "nonlocal",
        "def f():\n    n = 0\n    def g():\n        nonlocal n\n        return n\n    return g",
        set(),
    ),
    ("closure reads enclosing local", "def f():\n    v = 1\n    return lambda: v", set()),
    ("comprehension target local", "def f():\n    return [i for i in range(3)]", {"range"}),
    (
        "comprehension target leaks nowhere",
        "def f():\n    xs = [i for i in ()]\n    return i",
        {"i"},
    ),
    ("first iterable is outer", "def f():\n    return [x for x in x]", {"x"}),
    ("walrus binds function", "def f():\n    [w := 1 for _ in ()]\n    return w", set()),
    (
        "class body not visible to methods",
        "class C:\n    x = 1\n    def m(self):\n        return x",
        {"x"},
    ),
    ("default evaluated outside", "def f(a=DEFAULT): return a", {"DEFAULT"}),
    ("decorator evaluated outside", "@deco\ndef f(): pass", {"deco"}),
    ("annotations evaluated outside", "def f(a: T) -> R: pass", {"T", "R"}),
    ("except alias local", "def f():\n    try: pass\n    except E as e:\n        return e", {"E"}),
    ("with alias local", "def f():\n    with cm() as c:\n        return c", {"cm"}),
    (
        "match capture local",
        "def f(v):\n    match v:\n        case [a, *rest]:\n            return a, rest",
        set(),
    ),
    (
        "match mapping rest local",
        "def f(v):\n    match v:\n        case {'k': 1, **rest}:\n            return rest",
        set(),
    ),
    ("del makes local", "def f():\n    x = 1\n    del x", set()),
    ("lambda params", "f = lambda a, *b, c=D, **e: (a, b, c, e, F)", {"D", "F"}),
    ("nested function name local", "def f():\n    def g(): pass\n    return g", set()),
    ("module level reads", "x = y + z", {"y", "z"}),
]


@pytest.mark.parametrize(("label", "source", "expected"), CASES, ids=[c[0] for c in CASES])
def test_global_refs(label: str, source: str, expected: set[str]) -> None:
    assert refs(source) == expected, label


def test_local_import_resolves_to_target() -> None:
    stmt = ast.parse("def f():\n    from .sib import helper\n    return helper()").body[0]
    out = collect(stmt, ImportContext("pkg.mod", is_package=False))
    assert [(r.base, r.attrs) for r in out.refs] == [(FromTarget("pkg.sib", "helper"), ())]
    assert out.eager_loads == set()
    assert "pkg.sib" in out.loads


def test_import_bindings_and_relative_resolution() -> None:
    ctx = ImportContext("pkg.sub.mod", is_package=False)
    stmt = ast.parse("from ..top import a as b").body[0]
    assert module_bindings(stmt, ctx) == {"b": [FromTarget("pkg.top", "a")]}
    stmt = ast.parse("import x.y.z").body[0]
    assert module_bindings(stmt, ctx) == {"x": [ModuleTarget("x")]}
    stmt = ast.parse("import x.y as q").body[0]
    assert module_bindings(stmt, ctx) == {"q": [ModuleTarget("x.y")]}


def test_package_relative_import_uses_package_itself() -> None:
    ctx = ImportContext("pkg", is_package=True)
    assert ctx.absolute(1, "mod") == "pkg.mod"
    assert ctx.absolute(2, "mod") is None
    assert ImportContext("__main__", is_package=False).absolute(1, "x") is None


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("globals()['x']", "globals"),
        ("eval('1')", "eval"),
        ("exec('1')", "exec"),
        ("vars()", "vars"),
        ("import sys\nsys.modules['x']", "sys.modules"),
        ("__import__(name)", "import"),
        ("import importlib\nimportlib.import_module('pkg.' + n)", "import-prefix"),
        ("from importlib import import_module\nimport_module(f'pkg.{n}')", "import-prefix"),
    ],
)
def test_dynamic_uses_detected(source: str, kind: str) -> None:
    tree = ast.parse(source)
    kinds = {
        d.kind for s in tree.body for d in collect(s, ImportContext("m", is_package=False)).dynamic
    }
    assert kind in kinds


def test_vars_with_argument_is_not_dynamic() -> None:
    out = collect(ast.parse("vars(obj)").body[0], ImportContext("m", is_package=False))
    assert out.dynamic == []


def test_shadowed_dynamic_builtin_is_not_dynamic() -> None:
    src = "def f(eval):\n    return eval('x')"
    out = collect(ast.parse(src).body[0], ImportContext("m", is_package=False))
    assert out.dynamic == []
