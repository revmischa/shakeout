"""Stable library API. The CLI and adapters (hatch hook, CI actions, uv) build on this."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from shakeout.emit.archive import ArchiveError, write_lambda, write_pyz
from shakeout.emit.single import write_single
from shakeout.emit.tree import write_tree
from shakeout.options import Options
from shakeout.project import BuildError
from shakeout.project import analyze as _analyze
from shakeout.report import render_report
from shakeout.result import ShakeResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from shakeout.emit import BuildResult

__all__ = [
    "TARGETS",
    "ArchiveError",
    "BuildError",
    "BuildResult",
    "Options",
    "ShakeResult",
    "analyze",
    "build",
    "render_report",
]

_WRITERS: dict[str, Callable[[ShakeResult, Path], BuildResult]] = {
    "single": write_single,
    "tree": write_tree,
    "pyz": write_pyz,
    "lambda": write_lambda,
}
TARGETS = tuple(_WRITERS)


def analyze(entry: str | Path, options: Options | None = None) -> ShakeResult:
    """Analyze the program rooted at ``entry`` without writing anything."""
    return _analyze(Path(entry), options)


def build(
    entry: str | Path,
    out: str | Path,
    *,
    target: str = "single",
    options: Options | None = None,
) -> BuildResult:
    """Analyze ``entry`` and write a ``target`` artifact into the directory ``out``.

    Raises:
        ValueError: ``target`` is not one of ``TARGETS``.
        BuildError: The program cannot be analyzed (e.g. a syntax error).
        ArchiveError: The target does not fit the entry (e.g. ``lambda`` without exports).
    """
    writer = _WRITERS.get(target)
    if writer is None:
        msg = f"unknown target {target!r}; expected one of {TARGETS}"
        raise ValueError(msg)
    return writer(analyze(entry, options), Path(out))
