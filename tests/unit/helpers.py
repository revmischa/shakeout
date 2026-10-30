from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from shakeout.api import Options, ShakeResult, analyze
from tests.harness.project import write_project

if TYPE_CHECKING:
    from pathlib import Path


def shake(
    tmp_path: Path, files: dict[str, str], entry: str = "main.py", **opts: object
) -> ShakeResult:
    root = write_project(tmp_path / "proj", files)
    return analyze(root / entry, Options(**opts))  # type: ignore[arg-type]


def lib_statements(tmp_path: Path, source: str):
    """Analyze ``source`` as module ``lib`` imported (for effects) by an empty entry."""
    result = shake(tmp_path, {"main.py": "import lib\n", "lib.py": textwrap.dedent(source)})
    return result.modules["lib"].statements
