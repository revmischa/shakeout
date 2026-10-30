"""Build options (mirrors ``[tool.shakeout]`` in pyproject.toml)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

_LIST_FIELDS = ("keep", "include", "pure_calls", "roots", "search_path", "exports")


@dataclass(frozen=True)
class Options:
    """Everything that changes what is kept or how it is written.

    Attributes:
        keep: Modules (``pkg.mod``) or symbols (``pkg.mod:name``) to keep even if unreferenced.
        include: Third-party top-level packages to bundle and shake (``"*"`` for all).
        pure_calls: Qualified callables whose calls have no side effects.
        assume_pure_bases: Assume unknown base classes do not register subclasses.
        shake: Remove unreachable code (False bundles everything that is imported).
        roots: Extra first-party source directories.
        search_path: Extra directories to find third-party packages in (e.g. a staged site).
        exports: Names in the entry module that outside code calls (a Lambda handler). Setting
            this makes the entry a library module: it is imported, not run as ``__main__``.
        tombstones: Explain removed names when they are accessed at runtime.
        embed_source: Register bundled sources with linecache so tracebacks show code lines.
        minify: Drop comments and blank lines (line numbers stay exact).
        strip_docstrings: Also drop docstrings, like ``python -OO`` (changes ``__doc__``).
        copy_dependencies: Ship third-party distributions that are not shaken (tree targets).
    """

    keep: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    pure_calls: tuple[str, ...] = ()
    assume_pure_bases: bool = False
    shake: bool = True
    roots: tuple[str, ...] = ()
    search_path: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()
    tombstones: bool = True
    embed_source: bool = True
    minify: bool = False
    strip_docstrings: bool = False
    copy_dependencies: bool = True

    def __post_init__(self) -> None:
        for name in _LIST_FIELDS:
            value: object = getattr(self, name)
            if isinstance(value, str) or not isinstance(value, Iterable):
                msg = f"option {name!r} must be a list of strings"
                raise TypeError(msg)
            items: Iterable[object] = value  # pyright: ignore[reportUnknownVariableType]
            object.__setattr__(self, name, tuple(str(v) for v in items))

    @property
    def library(self) -> bool:
        """Whether the entry is imported as a module rather than run as a script."""
        return bool(self.exports)
