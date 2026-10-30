"""Hermetic subprocess execution of Python programs for differential tests."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    def describe(self) -> str:
        return f"exit={self.returncode}\n--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}"


def hermetic_env() -> dict[str, str]:
    """A minimal, deterministic environment: no PYTHONPATH, fixed hash seed, no colors."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHON_COLORS": "0",
        "NO_COLOR": "1",
        "LC_ALL": "C.UTF-8",
    }


def run_python(
    script: Path,
    *,
    cwd: Path,
    argv: tuple[str, ...] = (),
    site: bool = False,
    extra_flags: tuple[str, ...] = (),
    timeout: float = 60,
) -> RunResult:
    """Run ``script`` in a fresh interpreter.

    ``-E -s`` ignore the environment and user site-packages. Unless ``site`` is set, ``-S`` also
    disables site-packages so a bundle cannot silently import from the dev environment. ``-I`` is
    not used because it would stop the script's own directory from being put on ``sys.path``,
    which the *original* (unbundled) program relies on.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    flags = ["-E", "-s", *([] if site else ["-S"]), *extra_flags]
    proc = subprocess.run(
        [sys.executable, *flags, str(script), *argv],
        cwd=cwd,
        env=hermetic_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return RunResult(proc.returncode, proc.stdout, proc.stderr)
