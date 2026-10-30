"""Soundness oracle: code that ran in the original program must not have been removed."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tests.harness.run import RunResult, run_python

TRACER = Path(__file__).with_name("tracer_main.py")


class StatementLike(Protocol):
    @property
    def origin(self) -> Path: ...
    @property
    def start(self) -> int: ...
    @property
    def end(self) -> int: ...
    @property
    def kept(self) -> bool: ...
    @property
    def id(self) -> str: ...


@dataclass(frozen=True)
class Violation:
    statement_id: str
    function: str
    firstlineno: int
    why: str

    def __str__(self) -> str:
        return f"{self.statement_id}: {self.function} (line {self.firstlineno}) {self.why}"


def trace_execution(
    script: Path, *, cwd: Path, argv: tuple[str, ...] = (), site: bool = False
) -> tuple[RunResult, list[dict[str, Any]]]:
    out = cwd / "trace.json"
    result = run_python(TRACER, cwd=cwd, argv=(str(out), str(script), *argv), site=site)
    records: list[dict[str, Any]] = json.loads(out.read_text()) if out.exists() else []
    return result, records


def check_soundness(
    records: list[dict[str, Any]], statements: list[StatementLike]
) -> list[Violation]:
    """Return every recorded execution that falls inside a removed top-level statement."""
    by_file: dict[str, list[StatementLike]] = {}
    for st in statements:
        by_file.setdefault(os.path.realpath(st.origin), []).append(st)
    violations: list[Violation] = []
    for rec in records:
        file_stmts = by_file.get(rec["file"], [])
        for st in file_stmts:
            if not (st.start <= rec["firstlineno"] <= st.end) or st.kept:
                continue
            if rec["free"]:
                why = "was called at runtime but its statement was removed"
            else:
                # Execution triggered from module level is fine only if every triggering line is
                # itself inside a removed statement (then that whole execution is gone too).
                live_lines = [
                    ln
                    for ln in rec["module_lines"]
                    if not any(o.start <= ln <= o.end and not o.kept for o in file_stmts)
                ]
                if not live_lines:
                    continue
                why = f"ran from kept module-level code at line(s) {live_lines} but was removed"
            violations.append(Violation(st.id, rec["name"], rec["firstlineno"], why))
    return violations
