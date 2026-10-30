"""Differential fuzzing: random programs must behave identically after bundling, and nothing that
ran in the original may have been removed. A failing (shrunk) program is saved as a ready-to-review
case file under tests/fuzz/failures/ so it can become a permanent regression case."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings

from shakeout.api import analyze, build
from tests.harness.project import write_project
from tests.harness.run import run_python
from tests.harness.soundness import check_soundness, trace_execution
from tests.harness.tracebacks import normalize_stderr

from .generator import Program, programs, render

FAILURES = Path(__file__).with_name("failures")
EXAMPLES = int(os.environ.get("SHAKEOUT_FUZZ_EXAMPLES", "40"))


def _save_failure(files: dict[str, str]) -> Path:
    FAILURES.mkdir(exist_ok=True)
    path = FAILURES / "last_failure.case"
    body = ['description = "fuzz failure (review, rename, and move into tests/cases/fuzz/)"']
    for name in ["main.py", *sorted(n for n in files if n != "main.py")]:
        body.append(f"=== {name}")
        body.append(files[name].rstrip("\n"))
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def check_program(files: dict[str, str]) -> None:
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="shakeout-fuzz-")))
    try:
        src = write_project(root / "src", files, dedent=False)
        original = run_python(src / "main.py", cwd=root / "c0")
        assert original.returncode == 0, original.describe()
        for target in ("single", "tree"):
            built = build(src / "main.py", root / target, target=target)
            got = run_python(built.run_path, cwd=root / f"c-{target}")
            assert got.stdout == original.stdout, f"{target} stdout differs\n{got.describe()}"
            assert got.returncode == 0, got.describe()
            assert normalize_stderr(got.stderr, strip_prefixes=[str(root / target)],
                                    runtime_files=built.runtime_files) == ""
        result = analyze(src / "main.py")
        _, records = trace_execution(src / "main.py", cwd=root / "ct")
        violations = check_soundness(records, list(result.statements))
        assert not violations, "unsound: " + "; ".join(map(str, violations))
    except AssertionError:
        _save_failure(files)
        raise
    finally:
        shutil.rmtree(root, ignore_errors=True)


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(programs())
def test_random_programs_bundle_equivalently(prog: Program) -> None:
    check_program(render(prog))


@pytest.mark.nightly
@settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(programs())
def test_random_programs_long_campaign(prog: Program) -> None:
    check_program(render(prog))
