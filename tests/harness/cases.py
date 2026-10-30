"""The case catalog format.

A case is one ``.case`` file: a TOML header, then source files introduced by ``=== <path>`` lines::

    description = "unused function is removed"
    removed = ["lib:unused"]
    kept = ["lib:used"]
    stdout = "hi\\n"
    === main.py
    from lib import used
    used()
    === lib.py
    def used(): print("hi")
    def unused(): pass

Header keys (all optional except ``description``):

    entry               entry script, default "main.py"
    argv                list of command-line arguments
    options             table passed to shakeout.api.Options
    removed / kept      symbols "module:name" ("__main__:name" for the entry)
    dropped_modules     modules discovered but not shipped
    bundled_modules     modules that must be shipped
    warnings            substrings that must each appear in some diagnostic
    stdout / exit_code  expected behavior of the *original* program (sanity check of the case)
    equivalent          default true: bundle must behave exactly like the original
    noshake_equivalent  default = equivalent: a bundle built without shaking is equivalent
    bundle_exit_code    when equivalent = false
    bundle_stderr_contains  substrings expected in bundle stderr when equivalent = false
    soundness           default true: run the dynamic soundness oracle
    targets             default ["single", "tree", "pyz", "minify"]
    min_python          e.g. "3.12"
    xfail               reason string: a documented, known limitation
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CASES_DIR = Path(__file__).resolve().parent.parent / "cases"
_KNOWN_KEYS = {
    "description",
    "entry",
    "noshake_equivalent",
    "argv",
    "options",
    "removed",
    "kept",
    "dropped_modules",
    "bundled_modules",
    "warnings",
    "stdout",
    "exit_code",
    "equivalent",
    "bundle_exit_code",
    "bundle_stderr_contains",
    "soundness",
    "targets",
    "min_python",
    "xfail",
}


@dataclass(frozen=True)
class Case:
    id: str
    path: Path
    description: str
    files: dict[str, str]
    entry: str = "main.py"
    argv: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict[str, Any])
    removed: frozenset[str] = frozenset()
    kept: frozenset[str] = frozenset()
    dropped_modules: frozenset[str] = frozenset()
    bundled_modules: frozenset[str] = frozenset()
    warnings: tuple[str, ...] = ()
    stdout: str | None = None
    exit_code: int = 0
    equivalent: bool = True
    noshake_equivalent: bool = True
    bundle_exit_code: int | None = None
    bundle_stderr_contains: tuple[str, ...] = ()
    soundness: bool = True
    targets: tuple[str, ...] = ("single", "tree", "pyz", "minify")
    min_python: tuple[int, int] = (3, 10)
    xfail: str | None = None

    @property
    def golden_path(self) -> Path:
        return self.path.with_suffix(".golden")


class CaseFormatError(ValueError):
    pass


def parse_case(path: Path, case_id: str) -> Case:
    text = path.read_text(encoding="utf-8")
    header_lines: list[str] = []
    files: dict[str, list[str]] = {}
    current: list[str] | None = None
    # Split on "\n" only: case sources may contain \f, \v and other str.splitlines separators.
    for line in (part + "\n" for part in text.split("\n")[:-1]):
        if line.startswith("=== "):
            name = line[4:].strip()
            if name in files:
                msg = f"{path}: duplicate file {name}"
                raise CaseFormatError(msg)
            current = files[name] = []
        elif current is None:
            header_lines.append(line)
        else:
            current.append(line)
    try:
        header = tomllib.loads("".join(header_lines))
    except tomllib.TOMLDecodeError as exc:
        msg = f"{path}: bad header: {exc}"
        raise CaseFormatError(msg) from exc
    unknown = set(header) - _KNOWN_KEYS
    if unknown:
        msg = f"{path}: unknown header keys {sorted(unknown)}"
        raise CaseFormatError(msg)
    if "description" not in header:
        msg = f"{path}: missing description"
        raise CaseFormatError(msg)
    entry = header.get("entry", "main.py")
    if entry not in files:
        msg = f"{path}: entry {entry} not among files {sorted(files)}"
        raise CaseFormatError(msg)
    major, minor = (int(x) for x in header.get("min_python", "3.10").split("."))
    return Case(
        id=case_id,
        path=path,
        description=header["description"],
        files={name: "".join(body) for name, body in files.items()},
        entry=entry,
        argv=tuple(header.get("argv", ())),
        options=dict(header.get("options", {})),
        removed=frozenset(header.get("removed", ())),
        kept=frozenset(header.get("kept", ())),
        dropped_modules=frozenset(header.get("dropped_modules", ())),
        bundled_modules=frozenset(header.get("bundled_modules", ())),
        warnings=tuple(header.get("warnings", ())),
        stdout=header.get("stdout"),
        exit_code=header.get("exit_code", 0),
        equivalent=header.get("equivalent", True),
        noshake_equivalent=header.get("noshake_equivalent", header.get("equivalent", True)),
        bundle_exit_code=header.get("bundle_exit_code"),
        bundle_stderr_contains=tuple(header.get("bundle_stderr_contains", ())),
        soundness=header.get("soundness", True),
        targets=tuple(header.get("targets", ("single", "tree", "pyz", "minify"))),
        min_python=(major, minor),
        xfail=header.get("xfail"),
    )


def discover_cases(root: Path = CASES_DIR) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(root.rglob("*.case")):
        case_id = path.relative_to(root).with_suffix("").as_posix()
        cases.append(parse_case(path, case_id))
    return cases
