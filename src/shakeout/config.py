"""Read [tool.shakeout] from pyproject.toml and merge with command-line flags."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_KEYS = {
    "entry": str, "target": str, "out": str, "keep": list, "include": list,
    "pure-calls": list, "assume-pure-bases": bool, "roots": list, "tombstones": bool,
}


class ConfigError(ValueError):
    pass


def find_pyproject(start: Path) -> Path | None:
    for directory in [start, *start.parents]:
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def load_config(pyproject: Path | None) -> dict[str, Any]:
    if pyproject is None:
        return {}
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{pyproject}: {exc}") from exc
    section = data.get("tool", {}).get("shakeout", {})
    if not isinstance(section, dict):
        raise ConfigError(f"{pyproject}: [tool.shakeout] must be a table")
    for key, value in section.items():
        if key not in _KEYS:
            raise ConfigError(f"{pyproject}: unknown [tool.shakeout] key {key!r}")
        if not isinstance(value, _KEYS[key]):
            raise ConfigError(f"{pyproject}: [tool.shakeout] {key} must be a {_KEYS[key].__name__}")
    base = pyproject.parent
    for key in ("entry", "out"):
        if key in section:
            section[key] = str(base / section[key])
    if "roots" in section:
        section["roots"] = [str(base / r) for r in section["roots"]]
    return section
