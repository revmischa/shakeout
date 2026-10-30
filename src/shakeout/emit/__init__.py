"""Bundle writers. Each turns a ShakeResult into an artifact on disk."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shakeout import __version__
from shakeout.emit.minify import drop_blank_lines, minify_lines

if TYPE_CHECKING:
    from pathlib import Path

    from shakeout.model import Module
    from shakeout.result import ShakeResult

_CODING = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")
Chunks = list[tuple[int, str]]


@dataclass
class BuildResult:
    """A written artifact.

    Attributes:
        result: The analysis that produced it.
        out: Output directory.
        run_path: What to execute or deploy (script, entry file, .pyz or Lambda .zip).
        runtime_files: Files whose frames belong to shakeout's runtime, not the program.
        manifest: Build manifest (also embedded in or written next to the artifact).
    """

    result: ShakeResult
    out: Path
    run_path: Path
    runtime_files: list[str] = field(default_factory=list[str])
    manifest: dict[str, object] = field(default_factory=dict[str, object])


class ChunkBuilder:
    """Turns kept statements into (original start line, text) chunks per module."""

    def __init__(self, result: ShakeResult) -> None:
        """Prepare to emit modules of ``result``."""
        self.result = result
        self.options = result.options
        self._minified: dict[str, tuple[list[str], frozenset[int]]] = {}

    def lines(self, mod: Module) -> tuple[list[str], frozenset[int]]:
        """Source lines to emit for ``mod`` (minified if requested) and verbatim rows."""
        if not (self.options.minify or self.options.strip_docstrings):
            return mod.lines, frozenset()
        if mod.name not in self._minified:
            self._minified[mod.name] = minify_lines(
                mod.source, strip_docstrings=self.options.strip_docstrings
            )
        return self._minified[mod.name]

    def chunks(self, mod: Module, kept: set[int] | None = None, *, stub: bool = True) -> Chunks:
        """Kept statements of ``mod`` as chunks, each ending in a newline.

        Each chunk starts right after the previous statement (kept or not) so comments and blank
        lines that precede a statement travel with it and vanish with it. ``kept`` overrides the
        kept set (used when two module names share one file).
        """
        lines, verbatim = self.lines(mod)
        out: Chunks = []
        prev_end = 0
        for st in mod.statements:
            if (st.index in kept) if kept is not None else st.kept:
                rw = self.result.rewrite(st) if kept is None else None
                if rw is not None:  # same line count, so later lines do not move
                    span = st.end - st.start + 1
                    text = "".join(lines[prev_end : st.start - 1]) + rw[0] + "\n" * span
                else:
                    text = "".join(lines[prev_end : st.end])
                if text and not text.endswith("\n"):
                    text += "\n"
                out.append((prev_end + 1, text))
            prev_end = st.end
        if stub and self.options.tombstones and self.options.shake:
            text = tombstone_stub(self.result, mod)
            if text:
                out.append((len(mod.lines) + 2, text))
        if self.options.minify:
            out = drop_blank_lines(out, verbatim)
        return out


def tombstones(result: ShakeResult, mod: Module) -> dict[str, tuple[str, int]]:
    """Names ``mod`` no longer binds, mapped to (file, line) of their definition."""
    out: dict[str, tuple[str, int]] = {}
    if mod.name not in result.loaded:
        return out
    for name, idxs in sorted(mod.binders.items()):
        if not any(result.binds_at_runtime(mod.statements[i], name) for i in idxs):
            out[name] = (mod.filename, mod.statements[idxs[0]].start)
    return out


def tombstone_stub(result: ShakeResult, mod: Module) -> str:
    """A PEP 562 ``__getattr__`` explaining removed names, unless the module defines one."""
    if mod.is_entry or "__getattr__" in mod.binders:
        return ""
    stones = tombstones(result, mod)
    if not stones:
        return ""
    table = {name: line for name, (_, line) in stones.items()}
    return (
        "def __getattr__(name, _removed=" + repr(table) + ", _module=__name__):\n"
        "    if name in _removed:\n"
        "        raise AttributeError(\n"
        "            f'module {_module!r} has no attribute {name!r}: removed by shakeout '\n"
        f"            f'(unreferenced; defined at {mod.filename}:{{_removed[name]}}). '\n"
        "            f'Keep it with: keep = [\\'{_module}:{name}\\']', name=name)\n"
        "    raise AttributeError(f'module {_module!r} has no attribute {name!r}', name=name)\n"
    )


def build_source(chunks: Chunks) -> str:
    """Reassemble chunks into source with original line numbers (newline padding)."""
    parts: list[str] = []
    line = 1
    for start, text in chunks:
        if start > line:
            parts.append("\n" * (start - line))
            line = start
        parts.append(text)
        line += text.count("\n")
    return "".join(parts)


def manifest(result: ShakeResult, contents: dict[str, str], target: str) -> dict[str, object]:
    """Build manifest: identity, modules, removals and what runtime diagnostics need."""
    digest = hashlib.sha256(target.encode())
    for name in sorted(contents):
        digest.update(name.encode() + b"\0" + contents[name].encode() + b"\0")
    modules: dict[str, object] = {}
    for name in sorted(result.loaded):
        mod = result.modules[name]
        modules[name] = {
            "file": mod.filename,
            "sha256": hashlib.sha256(contents.get(name, "").encode()).hexdigest()[:16],
            "statements": len(mod.statements),
            "kept": sum(st.kept for st in mod.statements),
        }
    dynamic = sorted(
        f"{result.modules[d.module].filename}:{d.line}"
        for d in result.diagnostics
        if d.message.startswith("dynamic import at ") and d.module in result.modules
    )
    stones = {
        name: {k: list(v) for k, v in tombstones(result, result.modules[name]).items()}
        for name in sorted(result.loaded)
    }
    data: dict[str, object] = {
        "shakeout": __version__,
        "build_id": digest.hexdigest()[:12],
        "target": target,
        "entry": result.entry.name,
        "modules": modules,
        "dropped": sorted(result.dropped_modules),
        "dynamic_imports": dynamic,
        "tombstones": {k: v for k, v in stones.items() if v},
        "embed_source": result.options.embed_source,
    }
    if result.options.exports:
        data["handlers"] = [
            f"{result.entry_module}.{e}" for e in result.options.exports if ":" not in e
        ]
    return data


def write_manifest(path: Path, data: dict[str, object]) -> None:
    """Write ``data`` as stable, human-readable JSON."""
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fix_coding_cookie(source: str) -> str:
    """Bundles are written as UTF-8; neutralize a different coding cookie in lines 1-2."""
    lines = source.split("\n", 2)
    for i in range(min(2, len(lines))):
        if _CODING.match(lines[i].encode("utf-8")):
            lines[i] = "# -*- coding: utf-8 -*-"
    return "\n".join(lines)
