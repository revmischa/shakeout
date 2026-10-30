"""Ship third-party distributions that are imported but not shaken (tree-shaped targets).

A distribution is copied whole: its packages, extension modules, data files and ``.dist-info``
(``importlib.metadata`` keeps working). For packages that *are* bundled and shaken via
``include`` only the ``.dist-info`` is copied.
"""

from __future__ import annotations

import shutil
from importlib.metadata import Distribution, distributions
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shakeout.result import ShakeResult

_NOT_SHIPPED = frozenset({"stdlib", "builtin"})


def needed_externals(result: ShakeResult) -> set[str]:
    """Top-level third-party modules imported by kept code."""
    needed: set[str] = set()
    for st in result.live_statements:
        for name in st.loads | st.required:
            top = name.partition(".")[0]
            if top in result.external_paths and result.externals.get(top) not in _NOT_SHIPPED:
                needed.add(top)
    return needed


def _site_of(top: str, location: Path) -> Path:
    """Directory that contains the top-level module or package ``top``."""
    if location.name.startswith("__init__.") or location.is_dir():
        return location.parent.parent if location.is_file() else location.parent
    return location.parent


def _owner(top: str, site: Path) -> Distribution | None:
    for dist in distributions(path=[str(site)]):
        names = (dist.read_text("top_level.txt") or "").split()
        files = dist.files or []
        if top in names or any(f.parts[0] in (top, f"{top}.py") or f.parts[0].startswith(f"{top}.") for f in files):
            return dist
    return None


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def copy_dependencies(result: ShakeResult, out: Path) -> list[str]:
    """Copy distributions needed at runtime into ``out``; return their names."""
    shipped: list[str] = []
    seen: set[str] = set()
    tops = {(t, True) for t in needed_externals(result)}
    tops |= {(n, False) for n in result.loaded if "." not in n and result.modules[n].origin
             and "site-packages" in result.modules[n].origin.parts}  # type: ignore[union-attr]
    for top, whole in sorted(tops):
        location = result.external_paths.get(top) or result.modules[top].origin
        if location is None:
            continue
        site = _site_of(top, location)
        dist = _owner(top, site)
        if dist is None:
            if whole:
                _copy_tree_or_file(site / top if (site / top).is_dir() else location, out)
                shipped.append(top)
            continue
        name = dist.metadata["Name"]
        if name in seen:
            continue
        seen.add(name)
        for rel in dist.files or []:
            src = Path(str(dist.locate_file(rel))).resolve()
            in_site = src.is_relative_to(site.resolve())
            is_meta = ".dist-info" in rel.parts[0]
            if in_site and src.is_file() and "__pycache__" not in rel.parts and (whole or is_meta):
                _copy_file(src, out / src.relative_to(site.resolve()))
        shipped.append(name)
    return shipped


def _copy_tree_or_file(src: Path, out: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, out / src.name, ignore=shutil.ignore_patterns("__pycache__"),
                        dirs_exist_ok=True)
    else:
        _copy_file(src, out / src.name)
