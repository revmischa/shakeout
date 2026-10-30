"""Core data model shared by analysis, shaking and emitting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ast
    from pathlib import Path


@dataclass(frozen=True)
class ModuleTarget:
    """``import x`` binds a module object."""

    module: str


@dataclass(frozen=True)
class FromTarget:
    """``from x import attr`` binds an attribute of x (or the submodule x.attr)."""

    module: str
    attr: str


@dataclass(frozen=True)
class NameTarget:
    """A reference to a global name of the referencing module."""

    name: str


Target = ModuleTarget | FromTarget | NameTarget
ImportTarget = ModuleTarget | FromTarget


@dataclass(frozen=True)
class Ref:
    """A use of ``base.attrs[0].attrs[1]...`` at ``line``."""

    base: Target
    attrs: tuple[str, ...]
    line: int
    in_function: bool = False


@dataclass(frozen=True)
class DynamicUse:
    """A construct the static analysis cannot see through."""

    kind: str  # "globals", "eval", "sys.modules", "import", "import-prefix"
    line: int
    detail: str = ""


@dataclass(frozen=True)
class Diagnostic:
    level: str  # "warning" | "info" | "error"
    message: str
    module: str = ""
    line: int = 0

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


@dataclass
class Statement:
    """A top-level statement (or group of statements sharing source lines)."""

    module: str
    index: int
    origin: Path
    start: int  # first line, including decorators
    end: int
    nodes: list[ast.stmt]
    kind: str
    binds: dict[str, list[ImportTarget | None]] = field(
        default_factory=dict[str, list[ImportTarget | None]]
    )
    refs: list[Ref] = field(default_factory=list[Ref])
    loads: set[str] = field(default_factory=set[str])
    eager_loads: set[str] = field(default_factory=set[str])
    required: set[str] = field(default_factory=set[str])  # names that must be modules
    dynamic: list[DynamicUse] = field(default_factory=list[DynamicUse])
    impure: str | None = None  # reason it has side effects, None when pure
    root: str | None = None  # reason it is always kept once its module loads
    dead: bool = False  # never executes (non-entry __main__ guard)
    pragma_pure: bool = False
    keep_pragma: bool = False
    kept: bool = False

    @property
    def id(self) -> str:
        return f"{self.module}@L{self.start}-L{self.end}"


@dataclass
class Module:
    name: str
    origin: Path | None
    filename: str  # display/compile filename, relative to its import root
    is_package: bool
    source: str
    is_entry: bool = False
    runs_as_main: bool = False  # the entry run as a script (its __main__ block is live)
    statements: list[Statement] = field(default_factory=list[Statement])
    binders: dict[str, list[int]] = field(default_factory=dict[str, list[int]])
    dynamic: list[DynamicUse] = field(default_factory=list[DynamicUse])
    file_uses: list[int] = field(default_factory=list[int])
    has_star_import: bool = False
    has_effects: bool = False
    whole_reason: str | None = None  # kept entirely, e.g. because it calls globals()

    @property
    def lines(self) -> list[str]:
        return split_lines(self.source)


def split_lines(source: str) -> list[str]:
    r"""Split on newlines only, like the tokenizer (str.splitlines also splits on \\f, \\v...)."""
    parts = source.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines
