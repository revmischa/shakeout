"""Table-driven tests: one row per purity rule (docs/PLAN.md section 4.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from .helpers import lib_statements

PURE = [
    ("plain def", "def f(x=1):\n    print(x)\n"),
    ("async def", "async def f():\n    await g()\n"),
    ("def with pure decorators", "import functools\n@functools.lru_cache(maxsize=None)\ndef f(): pass\n"),
    ("def with from-imported pure decorator", "from functools import wraps, cache\n@cache\ndef f(): pass\n"),
    ("staticmethod builtins", "@staticmethod\ndef f(): pass\n"),
    ("dataclass with field", "from dataclasses import dataclass, field\n@dataclass(frozen=True)\nclass C:\n    x: list = field(default_factory=list)\n"),
    ("class with plain body", "class C:\n    '''doc'''\n    X = 1\n    y: int\n    def m(self): pass\n    class Inner: pass\n"),
    ("Exception subclass", "class E(Exception):\n    pass\n"),
    ("Enum subclass", "import enum\nclass Color(enum.Enum):\n    RED = 1\n"),
    ("Generic[T] base", "import typing\nT = typing.TypeVar('T')\nclass B(typing.Generic[T]): pass\n"),
    ("collections.abc base", "from collections.abc import Mapping\nclass M(Mapping): pass\n"),
    ("constants", "A = 1\nB = (1, 'x', None)\nC = {'k': [1, {2}]}\nD = -A * 2 if A else 3\nE = f'{A}'\n"),
    ("annotated assignment", "X: int = 3\nY: str\n"),
    ("lambda", "f = lambda x: x + UNKNOWN\n"),
    ("pure factory calls", "import re, typing\nP = re.compile('x')\nT = typing.TypeVar('T', bound=int)\n"),
    ("builtin constructors", "S = frozenset({1, 2})\nR = range(10)\nD = dict(a=1)\n"),
    ("tuple unpacking", "a, (b, c) = 1, (2, 3)\n"),
    ("stdlib imports", "import os\nimport json as j\nfrom pathlib import Path\n"),
    ("TYPE_CHECKING block", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import not_installed_anywhere\n"),
    ("pass", "pass\n"),
]

IMPURE = [
    ("bare call", "print('x')\n", "expr statement"),
    ("unknown decorator", "def deco(f): return f\n@deco\ndef f(): pass\n", "decorator 'deco'"),
    ("decorator factory", "def route(p):\n    return lambda f: f\n@route('/')\ndef f(): pass\n", "decorator"),
    ("impure default", "def f(x=compute()): pass\n", "default argument"),
    ("metaclass keyword", "class C(metaclass=type): pass\n", "class keyword metaclass"),
    ("init_subclass kwargs", "class B:\n    def __init_subclass__(cls, **k): pass\nclass C(B, tag=1): pass\n", "class keyword tag"),
    ("subclass of registering base", "class B:\n    def __init_subclass__(cls): pass\nclass C(B): pass\n", "registers subclasses"),
    ("class body call", "class C:\n    print('x')\n", "class body contains Expr"),
    ("class attr impure init", "class C:\n    x = compute()\n", "class attribute initializer"),
    ("class decorator", "def reg(c): return c\n@reg\nclass C: pass\n", "class decorator"),
    ("unknown external base", "import socketserver\nclass H(socketserver.BaseRequestHandler): pass\n", "outside the bundle"),
    ("base is a variable", "Base = object\nclass C(Base): pass\n", "not a plain class definition"),
    ("attribute store", "import os\nos.environ_copy = 1\n", "attribute or item"),
    ("item store", "D = {}\nD['k'] = 1\n", "attribute or item"),
    ("unknown call", "X = compute()\n", "initializer 'compute()'"),
    ("sorted with key", "def k(x): return x\nX = sorted([1], key=k)\n", "initializer"),
    ("comprehension", "X = [i for i in range(3)]\n", "initializer"),
    ("for loop", "for i in range(3):\n    pass\n", "for statement"),
    ("try", "try:\n    import json\nexcept ImportError:\n    json = None\n", "try statement"),
    ("augmented assignment", "X = 1\nX += 1\n", "augassign"),
    ("del", "X = 1\ndel X\n", "delete"),
    ("missing module import", "import surely_not_installed_xyz\n", "cannot be found"),
    ("impure stdlib import", "import this\n", "external module 'this'"),
]


def _last(stmts):
    return stmts[-1]


@pytest.mark.parametrize(("label", "source"), PURE, ids=[r[0] for r in PURE])
def test_pure(tmp_path: Path, label: str, source: str) -> None:
    impure = [(st.id, st.impure) for st in lib_statements(tmp_path, source) if st.impure]
    assert impure == [], label


@pytest.mark.parametrize(("label", "source", "reason"), IMPURE, ids=[r[0] for r in IMPURE])
def test_impure(tmp_path: Path, label: str, source: str, reason: str) -> None:
    st = _last(lib_statements(tmp_path, source))
    assert st.impure is not None, label
    assert reason.lower() in st.impure.lower(), st.impure


def test_pure_pragma_overrides(tmp_path: Path) -> None:
    st = _last(lib_statements(tmp_path, "X = compute()  # shakeout: pure\n"))
    assert st.impure is None


def test_keep_pragma_is_root_and_effect(tmp_path: Path) -> None:
    st = _last(lib_statements(tmp_path, "def f():  # shakeout: keep\n    pass\n"))
    assert st.root and st.impure


def test_module_docstring_and_dunders_are_roots(tmp_path: Path) -> None:
    stmts = lib_statements(tmp_path, '"""doc"""\n__all__ = ["x"]\n__version__ = "1"\n')
    assert all(st.root for st in stmts)
    assert all(st.impure is None for st in stmts)


def test_main_guard_is_dead_outside_entry(tmp_path: Path) -> None:
    st = _last(lib_statements(tmp_path, "if __name__ == '__main__':\n    print(1)\n"))
    assert st.dead and st.kind == "main_guard"


def test_effects_propagate_through_imports(tmp_path: Path) -> None:
    from .helpers import shake

    result = shake(tmp_path, {
        "main.py": "import a\n",
        "a.py": "import b\n",
        "b.py": "import c\n",
        "c.py": "print('side effect')\n",
        "d.py": "",
    })
    assert all(result.modules[m].has_effects for m in ("a", "b", "c"))
    assert result.bundled_modules >= {"a", "b", "c"}


def test_cyclic_pure_modules_are_pure(tmp_path: Path) -> None:
    from .helpers import shake

    result = shake(tmp_path, {
        "main.py": "import a\n",
        "a.py": "import b\ndef fa(): return b\n",
        "b.py": "import a\ndef fb(): return a\n",
    })
    assert not result.modules["a"].has_effects and not result.modules["b"].has_effects
    assert result.dropped_modules == {"a", "b"}
