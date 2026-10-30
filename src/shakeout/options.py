"""Build options (mirrors [tool.shakeout] in pyproject.toml)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Options:
    keep: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    pure_calls: tuple[str, ...] = ()
    assume_pure_bases: bool = False
    shake: bool = True
    roots: tuple[str, ...] = ()
    tombstones: bool = True
    embed_source: bool = True
    extra: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("keep", "include", "pure_calls", "roots"):
            value = getattr(self, name)
            if isinstance(value, str) or not isinstance(value, Iterable):
                raise TypeError(f"option {name!r} must be a list of strings")
            object.__setattr__(self, name, tuple(str(v) for v in value))
