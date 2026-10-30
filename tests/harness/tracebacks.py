"""Traceback normalization so original and bundled runs can be compared frame by frame.

Bundles legitimately differ from the original in two ways that are *not* user-visible bugs:
the absolute path prefix of each file, and frames that belong to the import machinery or to the
bundle's own runtime loader. Everything else, including user frame file names, line numbers,
function names, the source line and the caret markers, must be identical.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_FRAME = re.compile(
    r'^(?P<lead>[ |+\-]*)File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.*))?$'
)


def _is_machinery(filename: str, runtime_files: Iterable[str]) -> bool:
    return filename.startswith("<frozen importlib") or filename in set(runtime_files)


def normalize_stderr(
    stderr: str,
    *,
    strip_prefixes: Iterable[str],
    runtime_files: Iterable[str] = (),
) -> str:
    """Strip path prefixes and drop import-machinery/runtime frames from every traceback."""
    prefixes = sorted({p.rstrip("/") + "/" for p in strip_prefixes}, key=len, reverse=True)
    runtime = [str(f) for f in runtime_files]
    out: list[str] = []
    skipping = False
    lead = ""
    for raw in stderr.splitlines():
        line = raw
        for prefix in prefixes:
            line = line.replace(prefix, "")
        match = _FRAME.match(line)
        if match:
            lead = match.group("lead")
            original_file = match.group("file")
            skipping = _is_machinery(original_file, runtime) or any(
                raw.find(f'"{r}"') != -1 for r in runtime
            )
            if skipping:
                continue
        elif skipping and line.startswith(lead + "  "):
            continue  # source/caret lines of a dropped frame
        else:
            skipping = False
        out.append(line)
    return "\n".join(out) + ("\n" if out else "")


def frames(stderr: str) -> list[tuple[str, int, str]]:
    """Extract (file, line, function) for every frame, in order."""
    result: list[tuple[str, int, str]] = []
    for line in stderr.splitlines():
        match = _FRAME.match(line)
        if match:
            result.append(
                (match.group("file"), int(match.group("line")), match.group("func") or "")
            )
    return result


def structure(normalized: str) -> str:
    """Drop source and caret lines, keeping frame headers and messages (for minified bundles)."""
    out: list[str] = []
    lead: str | None = None
    for line in normalized.splitlines():
        match = _FRAME.match(line)
        if match:
            lead = match.group("lead")
            out.append(line)
        elif lead is not None and line.startswith(lead + "  "):
            continue
        else:
            lead = None
            out.append(line)
    return "\n".join(out) + ("\n" if out else "")
