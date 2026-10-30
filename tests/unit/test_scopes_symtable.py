"""Differential test: scope analysis vs. CPython's own ``symtable``.

For every top-level statement of a corpus of real files, every name CPython's symbol table says
the statement reads from the module's global namespace must appear among shakeout's references.
Missing one would let shakeout remove a definition that is actually used (a soundness bug).

The per-file sample gives readable failures; the full-stdlib test is the real gate (~5s).
"""

from __future__ import annotations

import ast
import symtable
import sysconfig
import warnings
from pathlib import Path

import pytest

from shakeout.analysis.scopes import ImportContext, collect
from shakeout.model import NameTarget

# Names the compiler synthesizes (PEP 649/695 machinery); never user-visible globals.
COMPILER_INTERNAL = {"__class__", "__classdict__", "__classdictcell__",
                     "__conditional_annotations__", "__annotate__", "__type_params__"}
STDLIB = Path(sysconfig.get_paths()["stdlib"])
ALL_FILES = sorted(
    p for p in STDLIB.rglob("*.py")
    if "site-packages" not in p.parts and "test" not in p.parts and "tests" not in p.parts
)
SAMPLE = ALL_FILES[:: max(1, len(ALL_FILES) // 120)]


def _symtable_globals(source: str) -> set[str]:
    """Global names read anywhere inside a module consisting of one statement."""
    top = symtable.symtable(source, "<stmt>", "exec")
    # Python 3.12+ inlines comprehensions (PEP 709) and reports their iteration variables in
    # the enclosing table; they are comprehension-local, not globals.
    def comp_local(sym: symtable.Symbol) -> bool:
        return bool(getattr(sym, "is_comp_iter", lambda: False)())

    names = {s.get_name() for s in top.get_symbols() if s.is_referenced() and not comp_local(s)}
    stack = list(top.get_children())
    while stack:
        table = stack.pop()
        stack.extend(table.get_children())
        for sym in table.get_symbols():
            if sym.is_referenced() and not comp_local(sym) and not sym.is_parameter() and (
                sym.is_global() or sym.is_declared_global()
            ):
                names.add(sym.get_name())
    return names


def _shakeout_globals(stmt: ast.stmt) -> set[str]:
    out = collect(stmt, ImportContext("m", False))
    return {r.base.name for r in out.refs if isinstance(r.base, NameTarget)}


def _check_file(path: Path) -> list[str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return []
    lines = source.splitlines(keepends=True)
    problems = []
    prev_end = 0
    for stmt in tree.body:
        start = min([stmt.lineno, *(d.lineno for d in getattr(stmt, "decorator_list", []))])
        assert stmt.end_lineno is not None
        if start <= prev_end:
            prev_end = stmt.end_lineno
            continue  # shares a line with the previous statement
        prev_end = stmt.end_lineno
        segment = "".join(lines[start - 1 : stmt.end_lineno])
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                expected = _symtable_globals(segment)
        except SyntaxError:
            continue  # e.g. 'return' outside function is impossible here, but be safe
        missing = {n for n in expected - _shakeout_globals(stmt) if not n.startswith(".")} - COMPILER_INTERNAL
        if missing:
            problems.append(f"{path.relative_to(STDLIB)}:{start}: missing {sorted(missing)}")
    return problems


@pytest.mark.parametrize("path", SAMPLE, ids=lambda p: str(p.relative_to(STDLIB)))
def test_no_global_reference_is_missed_sample(path: Path) -> None:
    assert _check_file(path) == []


def test_no_global_reference_is_missed_full_stdlib() -> None:
    problems = [p for path in ALL_FILES for p in _check_file(path)]
    assert problems == [], "\n".join(problems[:50])
