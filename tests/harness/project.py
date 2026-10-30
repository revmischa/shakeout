"""Materialize small multi-file Python projects for tests."""

from __future__ import annotations

import textwrap
from pathlib import Path


def write_project(root: Path, files: dict[str, str], *, dedent: bool = True) -> Path:
    """Write ``files`` (relative path -> source) under ``root`` and return ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = textwrap.dedent(text) if dedent else text
        if body and not body.endswith("\n"):
            body += "\n"
        path.write_bytes(body.encode("utf-8"))
    return root
