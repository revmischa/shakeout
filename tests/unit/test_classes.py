"""Class statements whose bases make defining them observable (registration)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shakeout.api import Options
from tests.unit.helpers import lib_statements, shake

if TYPE_CHECKING:
    from pathlib import Path


def _class_impurity(tmp_path: Path, source: str, *, assume_pure_bases: bool = False) -> str | None:
    result = shake(
        tmp_path,
        {"main.py": "import lib\n", "lib.py": source},
        options=Options(assume_pure_bases=assume_pure_bases),
    )
    return next(
        st for st in reversed(result.modules["lib"].statements) if st.kind == "class"
    ).impure


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("import collections\nclass P(collections.namedtuple('P', 'x')): pass\n", None),
        (
            "def make(): return object\nclass P(make()): pass\n",
            "base 'make()' may have side effects",
        ),
        ("class P(UNKNOWN): pass\n", "base 'UNKNOWN' cannot be resolved"),
        ("Base = object\nclass P(Base): pass\n", "base 'Base' is not a plain class definition"),
        (
            "class M(type): pass\nclass B(metaclass=M): pass\nclass P(B): pass\n",
            "base 'B' registers subclasses (__init_subclass__/metaclass)",
        ),
        (
            "import socketserver\nclass P(socketserver.TCPServer): pass\n",
            (
                "base 'socketserver.TCPServer' comes from outside the bundle "
                "and may register subclasses"
            ),
        ),
        ("import typing\nclass P(typing.NamedTuple):\n    x: int\n", None),
        (
            "from collections.abc import Sized\nclass P(Sized):\n    def __len__(self): return 0\n",
            None,
        ),
        ("class P(Exception): pass\n", None),
    ],
)
def test_base_reasons(tmp_path: Path, source: str, reason: str | None) -> None:
    assert _class_impurity(tmp_path, source) == reason


@pytest.mark.parametrize(
    "source",
    [
        "class P(UNKNOWN): pass\n",
        "Base = object\nclass P(Base): pass\n",
        "import socketserver\nclass P(socketserver.TCPServer): pass\n",
    ],
)
def test_assume_pure_bases_trusts_unknown_bases(tmp_path: Path, source: str) -> None:
    assert _class_impurity(tmp_path, source, assume_pure_bases=True) is None


def test_registering_base_does_not_make_itself_impure(tmp_path: Path) -> None:
    stmts = lib_statements(tmp_path, "class B:\n    def __init_subclass__(cls): pass\n")
    assert stmts[-1].impure is None


def test_registration_propagates_through_bundled_hierarchy(tmp_path: Path) -> None:
    result = shake(
        tmp_path,
        {
            "main.py": "import impl\n",
            "base.py": (
                "class Root:\n    def __init_subclass__(cls): pass\n\nclass Mid(Root): pass\n"
            ),
            "impl.py": "from base import Mid\n\nclass Leaf(Mid): pass\n",
        },
    )
    leaf = result.modules["impl"].statements[-1]
    assert leaf.impure == "base 'Mid' registers subclasses (__init_subclass__/metaclass)"
