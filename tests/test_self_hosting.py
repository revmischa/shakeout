"""Self-hosting: shakeout bundles itself, and the bundled shakeout produces byte-identical bundles."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from shakeout.api import Options, build
from tests.harness.project import write_project
from tests.harness.run import run_python

SRC = Path(__file__).resolve().parents[1] / "src"
SAMPLE = {
    "main.py": "from lib import used\nimport plugins\nprint(used())\n",
    "lib.py": "def used():\n    return 'ok'\n\ndef unused():\n    pass\n",
    "plugins.py": "R = []\ndef reg(f):\n    R.append(f)\n    return f\n@reg\ndef p(): pass\n",
}


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(path).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


@pytest.mark.parametrize("tool_target", ["single", "tree"])
def test_bundled_shakeout_reproduces_its_own_output(tmp_path: Path, tool_target: str) -> None:
    root = Path(os.path.realpath(tmp_path))
    tool_src = write_project(
        root / "tool",
        {
            "shakeout_main.py": "import sys\nfrom shakeout.cli import main\nraise SystemExit(main())\n",
        },
    )
    tool = build(
        tool_src / "shakeout_main.py",
        root / "tool-out",
        target=tool_target,
        options=Options(roots=(str(SRC),)),
    )
    sample = write_project(root / "sample", SAMPLE)
    for target in ("single", "tree"):
        reference = build(sample / "main.py", root / f"ref-{target}", target=target)
        result = run_python(
            tool.run_path,
            cwd=root / "cwd",
            argv=(
                "build",
                str(sample / "main.py"),
                "-o",
                str(root / f"self-{target}"),
                "-t",
                target,
            ),
        )
        assert result.returncode == 0, result.describe()
        assert _digest(root / f"self-{target}") == _digest(reference.out), target
