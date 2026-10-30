"""Size reduction that keeps every remaining line at its original line number.

Comments, trailing whitespace and (optionally) docstrings are blanked; blank lines are then left
out of the stored chunks and recreated as padding at load time. Lines inside multi-line string
literals are content and are never touched.
"""

from __future__ import annotations

import ast
import io
import tokenize

from shakeout.model import split_lines


def minify_lines(source: str, *, strip_docstrings: bool) -> tuple[list[str], frozenset[int]]:
    """Minified copy of ``source`` with the same line count, and rows that must be kept verbatim.

    Returns:
        The minified lines (each ending in a newline) and the 1-based rows that are inside a
        multi-line string literal (blank or not, they are part of a value).
    """
    lines = [line.rstrip("\n") for line in split_lines(source)]
    verbatim: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            lines[row - 1] = lines[row - 1][:col]
        elif tok.start[0] != tok.end[0]:  # multi-line string (or f-string part)
            verbatim.update(range(tok.start[0], tok.end[0]))
    if strip_docstrings:
        for start, end, col, only in _docstrings(source, lines):
            lines[start - 1] = " " * col + ("pass" if only else "")
            for row in range(start + 1, end + 1):
                lines[row - 1] = ""
            verbatim.difference_update(range(start, end + 1))
    out = [line if row in verbatim else line.rstrip() for row, line in enumerate(lines, 1)]
    return [line + "\n" for line in out], frozenset(verbatim)


def drop_blank_lines(
    chunks: list[tuple[int, str]], verbatim: frozenset[int]
) -> list[tuple[int, str]]:
    """Split chunks around blank lines so only lines with content are stored."""
    out: list[tuple[int, str]] = []
    for start, text in chunks:
        run_start, run = 0, list[str]()
        for row, line in enumerate(split_lines(text), start):
            if line.strip() or row in verbatim:
                if not run:
                    run_start = row
                run.append(line)
            elif run:
                out.append((run_start, "".join(run)))
                run = []
        if run:
            out.append((run_start, "".join(run)))
    return out


def _docstrings(source: str, lines: list[str]) -> list[tuple[int, int, int, bool]]:
    """(start, end, column, is_only_statement) of docstrings that sit on their own lines."""
    found: list[tuple[int, int, int, bool]] = []
    tree = ast.parse(source)
    owners: list[ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = [tree]
    owners.extend(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for owner in owners:
        body = owner.body
        first = body[0] if body else None
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.end_lineno is not None
            and first.end_col_offset is not None
        ):
            continue
        start, end = first.lineno, first.end_lineno
        own_line = not lines[start - 1][: first.col_offset].strip()
        rest_of_line = lines[end - 1][first.end_col_offset :].strip()
        if own_line and not rest_of_line:
            only = len(body) == 1 and not isinstance(owner, ast.Module)
            found.append((start, end, first.col_offset, only))
    return found
