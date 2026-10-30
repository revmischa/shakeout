"""Bundle writers. Each turns a ShakeResult into an artifact on disk."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..model import Module
from ..project import ShakeResult

_CODING = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")


@dataclass
class BuildResult:
    result: ShakeResult
    out: Path
    run_path: Path
    runtime_files: list[str] = field(default_factory=list[str])
    manifest: dict[str, object] = field(default_factory=dict[str, object])


def module_chunks(
    result: ShakeResult, mod: Module, kept: set[int] | None = None, *, stub: bool = True
) -> list[tuple[int, str]]:
    """Kept statements as (original start line, text) chunks, each ending in a newline.

    Each chunk starts right after the previous statement (kept or not) so comments and blank
    lines that precede a statement travel with it and vanish with it.
    """
    lines = mod.lines
    chunks: list[tuple[int, str]] = []
    prev_end = 0
    for st in mod.statements:
        if (st.index in kept) if kept is not None else st.kept:
            rw = result.rewrite(st) if kept is None else None
            if rw is not None:
                # Same number of lines as the original statement, so later lines don't move.
                span = st.end - st.start + 1
                text = "".join(lines[prev_end : st.start - 1]) + rw[0] + "\n" * span
            else:
                text = "".join(lines[prev_end : st.end])
            if text and not text.endswith("\n"):
                text += "\n"
            chunks.append((prev_end + 1, text))
        prev_end = st.end
    if stub and result.options.tombstones and result.options.shake:
        text = tombstone_stub(result, mod, prev_end)
        if text:
            chunks.append((len(lines) + 2, text))
    return chunks


def tombstones(result: ShakeResult, mod: Module) -> dict[str, tuple[str, int]]:
    out: dict[str, tuple[str, int]] = {}
    if mod.name not in result.loaded:
        return out
    for name, idxs in sorted(mod.binders.items()):
        if any(result.binds_at_runtime(mod.statements[i], name) for i in idxs):
            continue
        first = mod.statements[idxs[0]]
        out[name] = (mod.filename, first.start)
    return out


def tombstone_stub(result: ShakeResult, mod: Module, _prev_end: int) -> str:
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


def build_source(chunks: list[tuple[int, str]]) -> str:
    parts: list[str] = []
    line = 1
    for start, text in chunks:
        if start > line:
            parts.append("\n" * (start - line))
            line = start
        parts.append(text)
        line += text.count("\n")
    return "".join(parts)


def manifest(result: ShakeResult, contents: dict[str, str]) -> dict[str, object]:
    digest = hashlib.sha256()
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
        f"{d.message.split(' at ', 1)[1].split(':', 2)[0]}:{d.line}"
        for d in result.diagnostics
        if d.message.startswith("dynamic import at ")
    )
    return {
        "shakeout": __version__,
        "build_id": digest.hexdigest()[:12],
        "entry": result.entry.name,
        "modules": modules,
        "dropped": sorted(result.dropped_modules),
        "dynamic_imports": dynamic,
        "tombstones": {
            name: {k: list(v) for k, v in tombstones(result, result.modules[name]).items()}
            for name in sorted(result.loaded)
            if tombstones(result, result.modules[name])
        },
        "embed_source": result.options.embed_source,
    }


def write_manifest(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fix_coding_cookie(source: str) -> str:
    """Bundles are written as UTF-8; neutralize a different coding cookie in lines 1-2."""
    lines = source.split("\n", 2)
    for i in range(min(2, len(lines))):
        if _CODING.match(lines[i].encode("utf-8")):
            lines[i] = "# -*- coding: utf-8 -*-"
    return "\n".join(lines)
