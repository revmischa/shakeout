"""Stage locked third-party dependencies with uv, for analysis and vendoring.

shakeout never resolves dependencies itself: ``uv export`` turns ``uv.lock`` into exact pins and
``uv pip install --target`` installs them for the *target* Python and platform (so a Linux
Lambda bundle can be built on macOS). Results are cached by lockfile content and target.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path


class StagingError(RuntimeError):
    """uv is missing or failed."""


def stage(
    project: Path,
    *,
    python_version: str | None = None,
    platform: str | None = None,
    cache_root: Path | None = None,
) -> Path:
    """Install the locked, non-dev dependencies of ``project`` into a cached directory.

    Returns:
        The directory to put on the search path (a ``site-packages``-like tree).

    Raises:
        StagingError: uv is not installed, there is no ``uv.lock``, or uv failed.
    """
    uv = shutil.which("uv")
    if uv is None:
        msg = "uv is not on PATH; install it from https://docs.astral.sh/uv/"
        raise StagingError(msg)
    lock = project / "uv.lock"
    if not lock.is_file():
        msg = f"{lock} not found; run 'uv lock' first"
        raise StagingError(msg)
    key = hashlib.sha256(
        lock.read_bytes() + f"\0{python_version}\0{platform}".encode()
    ).hexdigest()[:16]
    root = cache_root or project / ".shakeout" / "staging"
    site = root / key / "site-packages"
    if (site / ".complete").is_file():
        return site
    shutil.rmtree(site.parent, ignore_errors=True)
    site.mkdir(parents=True)
    requirements = site.parent / "requirements.txt"
    _run([uv, "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project",
          "--no-header", "--quiet", "-o", str(requirements)], cwd=project)
    install = [uv, "pip", "install", "--quiet", "--no-deps", "--target", str(site),
               "-r", str(requirements)]
    if python_version:
        install += ["--python-version", python_version]
    if platform:
        install += ["--python-platform", platform]
    _run(install, cwd=project)
    (site / ".complete").write_text(key)
    return site


def _run(argv: list[str], cwd: Path) -> None:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.returncode:
        msg = f"{' '.join(argv[:3])} failed:\n{proc.stderr.strip()}"
        raise StagingError(msg)
