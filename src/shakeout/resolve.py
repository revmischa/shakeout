"""Locate modules on a search path without importing (executing) anything."""

from __future__ import annotations

import os
import pkgutil
import sys
import sysconfig
from dataclasses import dataclass
from functools import cache
from importlib.machinery import EXTENSION_SUFFIXES, PathFinder
from pathlib import Path

# Stdlib modules whose import has visible effects; importing them is never "pure".
IMPURE_STDLIB = frozenset(
    {
        "this",
        "antigravity",
        "__hello__",
        "__phello__",
        "idlelib",
        "turtle",
        "tkinter",
        "readline",
        "rlcompleter",
        "site",
    }
)


@dataclass(frozen=True)
class Found:
    """A located module."""

    name: str
    origin: Path | None  # None for namespace packages
    is_package: bool
    search_locations: tuple[str, ...]
    kind: str  # "source" | "namespace" | "extension" | "other"


def is_stdlib(name: str) -> bool:
    """Whether ``name`` belongs to the standard library."""
    top = name.partition(".")[0]
    return top in sys.stdlib_module_names or top == "__future__"


def is_pure_external(name: str) -> bool:
    """Importing this non-bundled module is assumed to have no observable effects."""
    return is_stdlib(name) and name.partition(".")[0] not in IMPURE_STDLIB


class ModuleFinder:
    """Finds modules on a search path and decides which ones get bundled."""

    def __init__(self, search_path: list[str], roots: list[Path], include: frozenset[str]) -> None:
        self.search_path = [p for p in search_path if p]
        self.roots = [Path(os.path.realpath(r)) for r in roots]
        self.include = include
        self._find = cache(self._find_uncached)

    def find(self, name: str) -> Found | None:
        """Locate ``name`` (cached); None if it is not importable from the search path."""
        return self._find(name)

    def _find_uncached(self, name: str) -> Found | None:
        parent, _, _ = name.rpartition(".")
        if parent:
            pfound = self.find(parent)
            if pfound is None or not pfound.is_package:
                return None
            path = list(pfound.search_locations)
        else:
            if name in sys.builtin_module_names:
                return None
            path = self.search_path
        try:
            spec = PathFinder.find_spec(name, path)
        except (ImportError, ValueError):
            return None
        if spec is None:
            return None
        locations = tuple(spec.submodule_search_locations or ())
        is_package = spec.submodule_search_locations is not None
        if spec.origin is None or spec.origin == "namespace":
            return Found(name, None, is_package=True, search_locations=locations, kind="namespace")
        origin = Path(spec.origin)
        if origin.suffix == ".py":
            kind = "source"
        elif any(spec.origin.endswith(s) for s in EXTENSION_SUFFIXES):
            kind = "extension"
        else:
            kind = "other"
        return Found(name, origin, is_package, locations, kind)

    def bundleable(self, found: Found) -> bool:
        """Whether ``found`` is first-party (or included) source that shakeout bundles."""
        if found.kind not in ("source", "namespace"):
            return False
        top = found.name.partition(".")[0]
        if is_stdlib(found.name):
            return top in self.include
        if top in self.include or ("*" in self.include and found.kind == "source"):
            return True
        paths = [found.origin] if found.origin else [Path(p) for p in found.search_locations]
        for p in paths:
            real = Path(os.path.realpath(p))
            if {"site-packages", "dist-packages"} & set(real.parts):
                return False
            if any(real.is_relative_to(root) for root in self.roots):
                return True
        return False

    def submodules(self, found: Found) -> list[str]:
        """All importable submodules (recursively) of a package, sorted."""
        if not found.is_package:
            return []
        names: list[str] = []
        for info in pkgutil.iter_modules(list(found.search_locations)):
            full = f"{found.name}.{info.name}"
            names.append(full)
            if info.ispkg:
                sub = self.find(full)
                if sub is not None:
                    names.extend(self.submodules(sub))
        return sorted(set(names))


def display_filename(name: str, found: Found) -> str:
    """Path of the module relative to its import root, e.g. ``pkg/sub/__init__.py``."""
    base = name.replace(".", "/")
    if found.origin is None:  # namespace package
        return base + "/"
    if found.is_package:
        return f"{base}/{found.origin.name}"
    return base + found.origin.suffix


def project_root(start: Path) -> Path:
    """The directory of the nearest ``pyproject.toml`` at or above ``start``, else ``start``."""
    for directory in [start, *start.parents]:
        if (directory / "pyproject.toml").is_file():
            return directory
    return start


def stdlib_path() -> list[str]:
    """Standard-library directories of the running interpreter (never its site-packages)."""
    paths = sysconfig.get_paths()
    platstdlib = Path(paths["platstdlib"])
    dirs = [Path(paths["stdlib"]), platstdlib, platstdlib / "lib-dynload"]
    zips = [p for p in sys.path if p.endswith(".zip")]
    return list(dict.fromkeys([*(str(d) for d in dirs if d.is_dir()), *zips]))


def environment_site_dirs(project: Path) -> list[str]:
    """Third-party package directories of the project's environment.

    In order: an activated virtual environment other than the one shakeout runs in, a ``.venv``
    in the project, and the running interpreter's environment only if it lives inside the project
    (``uv run shakeout`` in a project that depends on shakeout). Packages that are merely installed
    wherever shakeout itself is installed are never used.
    """
    prefix = Path(sys.prefix).resolve()
    roots: list[Path] = []
    active = os.environ.get("VIRTUAL_ENV")
    if active and Path(active).resolve() != prefix:
        roots.append(Path(active))
    roots.append(project / ".venv")
    dirs = [d for root in roots for d in _venv_site_dirs(root)]
    if prefix.is_relative_to(project.resolve()):
        paths = sysconfig.get_paths()
        dirs += [paths["purelib"], paths["platlib"]]
    return list(dict.fromkeys(d for d in dirs if Path(d).is_dir()))


def _venv_site_dirs(root: Path) -> list[str]:
    lib = root / "lib"
    found = sorted(str(p) for p in lib.glob("python*/site-packages")) if lib.is_dir() else []
    windows = root / "Lib" / "site-packages"
    return [*found, *([str(windows)] if windows.is_dir() else [])]
