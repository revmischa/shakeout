"""Stable library API. CLI and future adapters (hatch hook, uv, CI actions) build on this."""

from __future__ import annotations

from pathlib import Path

from .emit import BuildResult
from .options import Options
from .project import BuildError, ShakeResult
from .project import analyze as _analyze
from .report import render_report

__all__ = ["BuildError", "BuildResult", "Options", "ShakeResult", "analyze", "build",
           "render_report"]

TARGETS = ("single", "tree")


def analyze(entry: str | Path, options: Options | None = None) -> ShakeResult:
    return _analyze(Path(entry), options)


def build(
    entry: str | Path,
    out: str | Path,
    *,
    target: str = "single",
    options: Options | None = None,
) -> BuildResult:
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {TARGETS}")
    result = analyze(entry, options)
    out = Path(out)
    if target == "single":
        from .emit.single import write_single

        return write_single(result, out)
    from .emit.tree import write_tree

    return write_tree(result, out)
