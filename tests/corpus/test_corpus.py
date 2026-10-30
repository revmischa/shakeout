"""Real-world corpus tier: pinned third-party libraries, vendored into a project and shaken.

Each driver exercises a library's main API. The bundle (single file and tree) must produce the
same output in an interpreter without site-packages, and the soundness oracle must find nothing
that ran in the original but was removed. Size reductions are written to tests/corpus/metrics.json.

Nightly tier: needs network on first run (packages are cached under .corpus/).
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib
from tests.harness.run import run_python
from tests.harness.soundness import check_soundness, trace_execution
from tests.harness.tracebacks import normalize_stderr

from shakeout.api import Options, analyze, build

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
CACHE = HERE.parent.parent / ".corpus" / f"py{sys.version_info.major}{sys.version_info.minor}"
DRIVERS = sorted((HERE / "drivers").glob("*_driver.py"))
METRICS = HERE / "metrics.json"

pytestmark = pytest.mark.nightly


@pytest.fixture(scope="session")
def corpus_site() -> Path:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with (CACHE.parent / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one xdist worker installs, the rest wait
        stamp = CACHE / ".requirements"
        if stamp.exists() and stamp.read_text() == REQUIREMENTS.read_text():
            return CACHE
        uv = shutil.which("uv")
        if uv is None:
            pytest.skip("uv is required to install the corpus")
        shutil.rmtree(CACHE, ignore_errors=True)
        subprocess.run(
            [
                uv,
                "pip",
                "install",
                "-q",
                "--target",
                str(CACHE),
                "--python",
                sys.executable,
                "-r",
                str(REQUIREMENTS),
            ],
            check=True,
        )
        stamp.write_text(REQUIREMENTS.read_text())
    return CACHE


OVERRIDES = HERE / "overrides.toml"


def _overrides(name: str) -> dict[str, Any]:
    table: dict[str, dict[str, Any]] = tomllib.loads(OVERRIDES.read_text())
    return table.get(name, {})


def _source_bytes(paths: list[Path]) -> int:
    return sum(p.stat().st_size for p in paths)


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda p: p.stem.removesuffix("_driver"))
def test_corpus_driver(driver: Path, corpus_site: Path, tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    src = root / "src"
    shutil.copytree(
        corpus_site,
        src,
        ignore=shutil.ignore_patterns("bin", "__pycache__", ".requirements", ".lock"),
    )
    entry = src / "main.py"
    shutil.copy(driver, entry)
    original = run_python(entry, cwd=root / "c0")
    assert original.returncode == 0, original.describe()

    options = Options(**_overrides(driver.stem.removesuffix("_driver")))
    result = analyze(entry, options)
    for target in ("single", "tree"):
        built = build(entry, root / target, target=target, options=options)
        got = run_python(built.run_path, cwd=root / f"c-{target}")
        assert got.returncode == original.returncode, got.describe()
        assert got.stdout == original.stdout, got.describe()
        assert normalize_stderr(
            got.stderr, strip_prefixes=[str(root / target)], runtime_files=built.runtime_files
        ) == normalize_stderr(original.stderr, strip_prefixes=[str(src)])

    _, records = trace_execution(entry, cwd=root / "ct")
    violations = check_soundness(records, list(result.statements))
    assert not violations, "\n".join(map(str, violations[:20]))

    bundled = [m for m in result.modules.values() if m.name in result.loaded and m.origin]
    before = _source_bytes(
        [m.origin for m in result.modules.values() if m.origin and not m.is_entry]
    )
    after = sum((root / "tree" / m.filename).stat().st_size for m in bundled if not m.is_entry)
    metrics: dict[str, object] = json.loads(METRICS.read_text()) if METRICS.exists() else {}
    metrics[driver.stem.removesuffix("_driver")] = {
        "modules_discovered": len(result.modules) - 1,
        "modules_bundled": len(result.loaded) - 1,
        "statements_kept": sum(st.kept for st in result.statements),
        "statements_total": len(result.statements),
        "bytes_reachable_modules": before,
        "bytes_bundled": after,
    }
    METRICS.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
