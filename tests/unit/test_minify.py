"""The minifier must not change what code does or where it is.

For every stdlib file: the minified source compiles to the same instructions, with the same
arguments, at the same line numbers. Docstring stripping must only change docstrings.
"""

from __future__ import annotations

import dis
import sysconfig
import types
import warnings
from pathlib import Path
from typing import cast

import pytest

from shakeout.emit import build_source
from shakeout.emit.minify import drop_blank_lines, minify_lines

STDLIB = Path(sysconfig.get_paths()["stdlib"])
FILES = sorted(p for p in STDLIB.rglob("*.py") if "site-packages" not in p.parts)


Shape = list[tuple[str, str, int | None]]


def _line(ins: dis.Instruction) -> int | None:
    positions = getattr(ins, "positions", None)  # Python 3.11+
    return positions.lineno if positions is not None else ins.starts_line


def _shape(code: types.CodeType) -> Shape:
    """Every instruction (nested code objects inlined) as (opname, argument, line)."""
    out: Shape = []
    for ins in dis.get_instructions(code):
        arg: object = ins.argval
        if isinstance(arg, types.CodeType):
            out.append((ins.opname, "<code>", _line(ins)))
            out.extend(_shape(arg))
            continue
        if isinstance(arg, frozenset):  # may print in a different order for identical code
            arg = sorted(map(repr, cast("frozenset[object]", arg)))
        out.append((ins.opname, repr(arg), _line(ins)))
    return out


def _compile(source: str, name: str) -> types.CodeType | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return compile(source, name, "exec", dont_inherit=True)
    except (SyntaxError, ValueError):
        return None


def _check(path: Path) -> str | None:
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    source = source.replace("\r\n", "\n")
    original = _compile(source, str(path))
    if original is None:
        return None
    lines, verbatim = minify_lines(source, strip_docstrings=False)
    minified = build_source(drop_blank_lines([(1, "".join(lines))], verbatim))
    again = _compile(minified, str(path))
    if again is None:
        return f"{path}: minified source does not compile"
    before, after = _shape(original), _shape(again)
    for i, (x, y) in enumerate(zip(before, after, strict=False)):
        if x != y:
            return f"{path}: instruction {i} differs: {x} != {y}"
    if len(before) != len(after):
        return f"{path}: instruction count differs"
    return None


SHARDS = 16


@pytest.mark.parametrize("shard", range(SHARDS))
def test_minify_preserves_code_and_line_numbers_across_stdlib(shard: int) -> None:
    problems = [p for path in FILES[shard::SHARDS] if (p := _check(path))]
    assert problems == [], "\n".join(problems[:20])


def test_minify_shrinks_and_keeps_string_contents() -> None:
    source = 'x = 1  # comment\n\n\ns = """a  \n\n  b"""  \n# only comment\ny = f"""{x}\n\n"""\n'
    lines, verbatim = minify_lines(source, strip_docstrings=False)
    chunks = drop_blank_lines([(1, "".join(lines))], verbatim)
    rebuilt = build_source(chunks)
    ns: dict[str, object] = {}
    exec(rebuilt, ns)  # noqa: S102
    assert ns["s"] == "a  \n\n  b"
    assert ns["y"] == "1\n\n"
    assert "comment" not in rebuilt
    assert sum(len(t) for _, t in chunks) < len(source)


@pytest.mark.parametrize(
    ("source", "expected_doc"),
    [
        ('def f():\n    """Doc."""\n', None),
        ('def f():\n    """Doc."""\n    return 1\n', None),
        ('def f(): "inline stays"\n', "inline stays"),
        ('class C:\n    """Doc."""\n    x = 1\n', None),
    ],
)
def test_strip_docstrings(source: str, expected_doc: str | None) -> None:
    lines, _ = minify_lines(source, strip_docstrings=True)
    ns: dict[str, object] = {}
    exec("".join(lines), ns)  # noqa: S102
    obj = ns.get("f", ns.get("C"))
    assert getattr(obj, "__doc__", None) == expected_doc
