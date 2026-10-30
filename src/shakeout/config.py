"""Read ``[tool.shakeout]`` from pyproject.toml."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

if TYPE_CHECKING:
    from pathlib import Path

KEYS: dict[str, type] = {
    "entry": str,
    "target": str,
    "out": str,
    "keep": list,
    "include": list,
    "exports": list,
    "pure-calls": list,
    "assume-pure-bases": bool,
    "roots": list,
    "search-path": list,
    "tombstones": bool,
    "embed-source": bool,
    "minify": bool,
    "strip-docstrings": bool,
    "copy-dependencies": bool,
    "uv": bool,
    "python-version": str,
    "platform": str,
}
_PATH_KEYS = ("entry", "out")
_PATH_LIST_KEYS = ("roots", "search-path")


class ConfigError(ValueError):
    """Invalid configuration or arguments."""


def find_pyproject(start: Path) -> Path | None:
    """Nearest ``pyproject.toml`` in ``start`` or its parents."""
    for directory in [start, *start.parents]:
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def load_config(pyproject: Path | None) -> dict[str, Any]:
    """The validated ``[tool.shakeout]`` table, with paths made absolute.

    Raises:
        ConfigError: The file is not valid TOML or the table has unknown keys or wrong types.
    """
    if pyproject is None:
        return {}
    try:
        data: dict[str, Any] = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except ValueError as exc:  # tomllib.TOMLDecodeError subclasses ValueError
        msg = f"{pyproject}: {exc}"
        raise ConfigError(msg) from exc
    tool: object = data.get("tool", {})
    raw: object = cast("dict[str, Any]", tool).get("shakeout", {}) if isinstance(tool, dict) else {}
    if not isinstance(raw, dict):
        msg = f"{pyproject}: [tool.shakeout] must be a table"
        raise ConfigError(msg)
    section = cast("dict[str, Any]", raw)
    for key, value in section.items():
        expected = KEYS.get(key)
        if expected is None:
            msg = f"{pyproject}: unknown [tool.shakeout] key {key!r}"
            raise ConfigError(msg)
        if not isinstance(value, expected):
            msg = f"{pyproject}: [tool.shakeout] {key} must be a {expected.__name__}"
            raise ConfigError(msg)
    base = pyproject.parent
    for key in _PATH_KEYS:
        if key in section:
            section[key] = str(base / section[key])
    for key in _PATH_LIST_KEYS:
        if key in section:
            section[key] = [str(base / r) for r in section[key]]
    section["_project"] = str(base)
    return section
