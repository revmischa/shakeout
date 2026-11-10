"""Self-hosting: shakeout bundles itself, and the bundled tool makes byte-identical bundles."""

from __future__ import annotations

import hashlib
import os
import sys
import sysconfig
from pathlib import Path

import pytest

from shakeout.api import Options, analyze, build
from shakeout.api import __all__ as api_all
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
            "shakeout_main.py": "from shakeout.cli import main\n\nraise SystemExit(main())\n",
        },
    )
    # On 3.10 shakeout reads TOML with tomli; bundle (and shake) it too. It lives in this test
    # environment, which shakeout deliberately does not search unless told to.
    legacy = sys.version_info < (3, 11)
    include = ("tomli",) if legacy else ()
    search = (sysconfig.get_paths()["purelib"],) if legacy else ()
    tool = build(
        tool_src / "shakeout_main.py",
        root / "tool-out",
        target=tool_target,
        options=Options(roots=(str(SRC),), include=include, search_path=search),
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


def test_shakeout_has_no_dead_code(tmp_path: Path) -> None:
    """Dogfooding: shaking shakeout from its CLI and public API removes nothing."""
    api_names = ", ".join(f"api.{name}" for name in api_all)
    entry = (
        write_project(
            tmp_path,
            {
                "uses.py": (
                    "from shakeout import api, cli\n\n"
                    f"USED = (cli.main, {api_names})  # shakeout: keep\n"
                ),
            },
        )
        / "uses.py"
    )
    result = analyze(entry, Options(roots=(str(SRC),)))
    removed = sorted(s for s in result.removed if not s.startswith("__main__:"))
    assert removed == [], "unreachable definitions in shakeout:\n" + "\n".join(removed)
