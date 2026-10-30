"""End-to-end CLI tests through ``python -m shakeout``."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

from tests.harness.project import write_project
from tests.harness.run import run_python

if TYPE_CHECKING:
    from pathlib import Path

FILES = {
    "main.py": "from lib import used\nprint(used())\n",
    "lib.py": """\
        def used():
            return helper()


        def helper():
            return 'ok'


        def unused():
            pass
        """,
}


def cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "shakeout", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_build_single_and_run(tmp_path: Path) -> None:
    src = write_project(tmp_path / "src", FILES)
    proc = cli("build", str(src / "main.py"), "-o", str(tmp_path / "out"), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "1/3" not in proc.stdout
    assert "modules" in proc.stdout
    result = run_python(tmp_path / "out" / "main.py", cwd=tmp_path / "elsewhere")
    assert result.stdout == "ok\n"
    assert "def unused" not in (tmp_path / "out" / "main.py").read_text()


def test_build_tree_from_pyproject(tmp_path: Path) -> None:
    src = write_project(
        tmp_path / "src",
        {
            **FILES,
            "pyproject.toml": """\
                [tool.shakeout]
                entry = "main.py"
                target = "tree"
                out = "build/app"
                keep = ["lib:unused"]
                """,
        },
    )
    proc = cli("build", cwd=src)
    assert proc.returncode == 0, proc.stderr
    lib = (src / "build" / "app" / "lib.py").read_text()
    assert "def unused" in lib
    manifest = json.loads((src / "build" / "app" / "shakeout-manifest.json").read_text())
    assert manifest["entry"] == "main.py"
    assert len(manifest["build_id"]) == 12


def test_report_and_why(tmp_path: Path) -> None:
    src = write_project(tmp_path / "src", FILES)
    proc = cli("report", str(src / "main.py"), "--json", cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert "lib:unused" in data["removed"]
    assert "lib:helper" in data["kept"]
    why = cli("why", str(src / "main.py"), "lib:helper", cwd=tmp_path)
    assert why.returncode == 0
    lines = why.stdout.splitlines()
    assert lines[0].startswith("lib@L5-L6")
    assert "main" in why.stdout
    removed = cli("why", str(src / "main.py"), "lib:unused", cwd=tmp_path)
    assert removed.returncode == 1
    assert "was removed" in removed.stdout


def test_errors_are_reported_cleanly(tmp_path: Path) -> None:
    src = write_project(tmp_path / "src", {"main.py": "import lib\n", "lib.py": "def broken(:\n"})
    proc = cli("build", str(src / "main.py"), "-o", str(tmp_path / "o"), cwd=tmp_path)
    assert proc.returncode == 2
    assert proc.stderr.startswith("shakeout: error: lib.py:1: syntax error")
    bad = write_project(
        tmp_path / "bad", {"main.py": "", "pyproject.toml": "[tool.shakeout]\nbogus = 1\n"}
    )
    proc = cli("build", str(bad / "main.py"), cwd=tmp_path)
    assert proc.returncode == 2
    assert "unknown [tool.shakeout] key 'bogus'" in proc.stderr
