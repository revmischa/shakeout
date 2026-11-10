"""Ship data files that live inside bundled first-party packages (tree-shaped targets).

Code like ``certifi.where()`` or ``open(os.path.join(os.path.dirname(__file__), "x.json"))``
reads files next to its module. For every bundled regular package, non-Python files in its
directory, and in subdirectories that are not packages themselves (``templates/``,
``data/``), are copied. Distributions from site-packages are handled by ``deps``.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from shakeout.result import ShakeResult

_SKIP_SUFFIXES = frozenset({".py", ".pyc", ".pyo"})
_SKIP_DIRS = frozenset({"__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"})


def copy_package_data(result: ShakeResult, out: Path) -> list[str]:
    """Copy data files of bundled first-party packages into ``out``; return their paths."""
    copied: list[str] = []
    for name in sorted(result.loaded):
        mod = result.modules[name]
        if not mod.is_package or mod.origin is None or _installed(mod.origin):
            continue
        package_dir = mod.origin.parent
        target = out / mod.filename.rpartition("/")[0]
        copied.extend(_copy_data(package_dir, target, out))
    return copied


def _installed(origin: Path) -> bool:
    return bool({"site-packages", "dist-packages"} & set(origin.parts))


def _copy_data(directory: Path, target: Path, out: Path) -> list[str]:
    copied: list[str] = []
    for entry in sorted(directory.iterdir()):
        if entry.name in _SKIP_DIRS:
            continue
        if entry.is_dir():
            if not (entry / "__init__.py").exists():  # a data directory, not a subpackage
                copied.extend(_copy_data(entry, target / entry.name, out))
        elif entry.suffix not in _SKIP_SUFFIXES:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target / entry.name)
            copied.append((target / entry.name).relative_to(out).as_posix())
    return copied
