"""Tests for the test harness itself. An oracle that cannot fail is worse than none."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.harness.cases import CaseFormatError, discover_cases, parse_case
from tests.harness.project import write_project
from tests.harness.run import run_python
from tests.harness.soundness import check_soundness, trace_execution
from tests.harness.tracebacks import frames, normalize_stderr


@dataclass(frozen=True)
class FakeStatement:
    origin: Path
    start: int
    end: int
    kept: bool
    id: str


PROGRAM = {
    "main.py": """\
        import lib
        lib.called()
        """,
    "lib.py": """\
        def helper(x):
            return x


        def called():
            print("called")


        def never():
            print("never")


        VALUE = sorted([3, 1], key=helper)
        """,
}


def _lib_statements(src: Path, *, kept: set[str]) -> list[FakeStatement]:
    spans = {"helper": (1, 2), "called": (5, 6), "never": (9, 10), "VALUE": (13, 13)}
    return [
        FakeStatement(src / "lib.py", a, b, name in kept, f"lib:{name}")
        for name, (a, b) in spans.items()
    ]


Traced = tuple[Path, list[dict[str, Any]]]


@pytest.fixture
def traced(tmp_path: Path) -> Traced:
    src = write_project(tmp_path / "src", PROGRAM)
    result, records = trace_execution(src / "main.py", cwd=tmp_path / "cwd")
    assert result.returncode == 0, result.describe()
    assert result.stdout == "called\n"
    return src, records


def test_soundness_flags_removed_function_that_was_called(traced: Traced) -> None:
    src, records = traced
    violations = check_soundness(records, _lib_statements(src, kept={"helper", "VALUE"}))
    assert [v.statement_id for v in violations] == ["lib:called"]


def test_soundness_ignores_definition_time_execution_of_removed_statement(traced: Traced) -> None:
    # helper only runs while VALUE's own statement executes; removing both is sound.
    src, records = traced
    assert check_soundness(records, _lib_statements(src, kept={"called"})) == []


def test_soundness_flags_helper_used_by_kept_statement(traced: Traced) -> None:
    src, records = traced
    violations = check_soundness(records, _lib_statements(src, kept={"called", "VALUE"}))
    assert [v.statement_id for v in violations] == ["lib:helper"]


def test_soundness_drop_everything_bundler_is_caught(traced: Traced) -> None:
    src, records = traced
    violations = check_soundness(records, _lib_statements(src, kept=set()))
    assert {v.statement_id for v in violations} == {"lib:called"}


def test_soundness_never_called_function_is_not_flagged(traced: Traced) -> None:
    src, records = traced
    stmts = _lib_statements(src, kept={"called", "helper", "VALUE"})
    assert check_soundness(records, stmts) == []


def test_run_python_is_hermetic(tmp_path: Path) -> None:
    src = write_project(
        tmp_path, {"p.py": "import os, sys\nprint(os.environ.get('PYTHONPATH'), sys.flags.no_site)"}
    )
    result = run_python(src / "p.py", cwd=tmp_path)
    assert result.stdout == "None 1\n"


TRACEBACK = """\
Traceback (most recent call last):
  File "/tmp/out/bundle.py", line 40, in <module>
    _shakeout_main()
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "/tmp/src/main.py", line 2, in <module>
    lib.boom()
    ~~~~~~~~^^
  File "/tmp/src/lib.py", line 6, in boom
    raise ValueError("x")
ValueError: x
"""


def test_normalize_strips_prefixes_and_runtime_frames() -> None:
    got = normalize_stderr(
        TRACEBACK, strip_prefixes=["/tmp/src", "/tmp/out"], runtime_files=["/tmp/out/bundle.py"]
    )
    assert got == (
        "Traceback (most recent call last):\n"
        '  File "main.py", line 2, in <module>\n'
        "    lib.boom()\n"
        "    ~~~~~~~~^^\n"
        '  File "lib.py", line 6, in boom\n'
        '    raise ValueError("x")\n'
        "ValueError: x\n"
    )


def test_normalize_detects_line_number_differences() -> None:
    a = normalize_stderr(TRACEBACK, strip_prefixes=["/tmp/src"])
    b = normalize_stderr(TRACEBACK.replace("line 6", "line 7"), strip_prefixes=["/tmp/src"])
    assert a != b


def test_frames_parses_all_frames() -> None:
    assert frames(TRACEBACK)[-1] == ("/tmp/src/lib.py", 6, "boom")


def test_case_parser_rejects_unknown_keys(tmp_path: Path) -> None:
    p = tmp_path / "x.case"
    p.write_text('description = "d"\nremovd = []\n=== main.py\npass\n')
    with pytest.raises(CaseFormatError, match="unknown header keys"):
        parse_case(p, "x")


def test_case_parser_requires_entry(tmp_path: Path) -> None:
    p = tmp_path / "x.case"
    p.write_text('description = "d"\n=== lib.py\npass\n')
    with pytest.raises(CaseFormatError, match="entry"):
        parse_case(p, "x")


def test_case_parser_reads_files_verbatim(tmp_path: Path) -> None:
    p = tmp_path / "x.case"
    p.write_text(
        'description = "d"\nremoved = ["lib:f"]\n'
        "=== main.py\nimport lib\n\n=== lib.py\ndef f(): pass\n"
    )
    case = parse_case(p, "x")
    assert case.files == {"main.py": "import lib\n\n", "lib.py": "def f(): pass\n"}
    assert case.removed == {"lib:f"}


def test_catalog_parses_and_ids_are_unique() -> None:
    cases = discover_cases()
    assert len({c.id for c in cases}) == len(cases)


GROUP_TRACEBACK = """\
  + Exception Group Traceback (most recent call last):
  |   File "/tmp/out/app.py", line 117, in <module>
  |     _shakeout_bootstrap()
  |     ~~~~~~~~~~~~~~~~~~~^^
  |   File "/tmp/src/main.py", line 4, in <module>
  |     run()
  | ExceptionGroup: many (1 sub-exception)
  +-+---------------- 1 ----------------
    | TypeError: 3
    +------------------------------------
"""


def test_normalize_handles_exception_group_frames() -> None:
    got = normalize_stderr(
        GROUP_TRACEBACK, strip_prefixes=["/tmp/src", "/tmp/out"], runtime_files=["/tmp/out/app.py"]
    )
    assert '"/tmp/out/app.py"' not in got
    assert "_shakeout_bootstrap" not in got
    assert '  |   File "main.py", line 4, in <module>\n  |     run()\n' in got
