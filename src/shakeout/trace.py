"""Re-attach source lines and removal notes to a traceback from a bundle.

Bundles keep original file names and line numbers, so a traceback from a bundle already points
at the right place. This restores the *original* text of each line (minified bundles show lines
without comments; ``embed_source = false`` or log pipelines may drop them entirely) by reading
the original source tree, and explains names that shakeout removed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from shakeout.model import split_lines

if TYPE_CHECKING:
    from pathlib import Path

_FRAME = re.compile(r'^(?P<lead>[ |]*)File "(?P<file>[^"]+)", line (?P<line>\d+)')
_NAME_ERROR = re.compile(r"^NameError: name '(?P<name>[^']+)' is not defined")
_IMPORT_NAME = re.compile(r"^ImportError: cannot import name '(?P<name>[^']+)' from '(?P<mod>[^']+)'")


def symbolicate(traceback: str, manifest: dict[str, object], root: Path) -> str:
    """Return ``traceback`` with original source lines and removal notes."""
    files = _module_files(manifest)
    cache: dict[str, list[str]] = {}
    out: list[str] = []
    lines = traceback.splitlines()
    skip_next = False
    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        out.append(line)
        match = _FRAME.match(line)
        if not match or match.group("file") not in files:
            continue
        source = _line(root, match.group("file"), int(match.group("line")), cache)
        if not source:
            continue
        indent = match.group("lead") + "  "
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        skip_next = (
            nxt.startswith(indent) and not _FRAME.match(nxt) and not set(nxt.strip()) <= set("~^")
        )
        out.append(f"{indent}{source.strip()}")
    out.extend(_notes(lines, manifest))
    return "\n".join(out) + "\n"


def _module_files(manifest: dict[str, object]) -> set[str]:
    modules = manifest.get("modules")
    if not isinstance(modules, dict):
        return set()
    out: set[str] = set()
    for info in modules.values():  # pyright: ignore[reportUnknownVariableType]
        if isinstance(info, dict) and isinstance(info.get("file"), str):  # pyright: ignore[reportUnknownMemberType]
            out.add(str(info["file"]))  # pyright: ignore[reportUnknownArgumentType]
    return out


def _line(root: Path, file: str, number: int, cache: dict[str, list[str]]) -> str:
    if file not in cache:
        path = root / file
        cache[file] = split_lines(path.read_text(encoding="utf-8")) if path.is_file() else []
    lines = cache[file]
    return lines[number - 1] if 0 < number <= len(lines) else ""


def _notes(lines: list[str], manifest: dict[str, object]) -> list[str]:
    stones = manifest.get("tombstones")
    if not isinstance(stones, dict) or not lines:
        return []
    last = lines[-1]
    notes: list[str] = []
    name_error = _NAME_ERROR.match(last)
    import_error = _IMPORT_NAME.match(last)
    for module, names in stones.items():  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(names, dict):
            continue
        for name, where in names.items():  # pyright: ignore[reportUnknownVariableType]
            hit = (name_error and name_error.group("name") == name) or (
                import_error and import_error.group("name") == name and import_error.group("mod") == module
            )
            if hit and isinstance(where, list) and len(where) == 2:  # noqa: PLR2004 - (file, line)
                notes.append(
                    f"shakeout: {name!r} was removed from {where[0]}:{where[1]}; "
                    f"keep it with: keep = ['{module}:{name}']"
                )
    return notes
