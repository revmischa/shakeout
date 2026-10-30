"""Runs the case catalog (tests/cases/**/*.case). See tests/harness/cases.py for the format.

Every case is checked from five independent angles:

* expectations   symbols removed/kept, modules dropped/bundled, diagnostics
* original       the unbundled program behaves as the case says (guards against bad cases)
* equivalence    each bundle target behaves exactly like the original, tracebacks included;
                 "noshake" bundles without removal to separate loader bugs from analysis bugs
* soundness      nothing that executed in the original was removed (dynamic oracle)
* golden         the reviewed analysis report has not changed unexpectedly
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.harness.cases import Case, discover_cases
from tests.harness.project import write_project
from tests.harness.run import RunResult, run_python
from tests.harness.soundness import check_soundness, trace_execution
from tests.harness.tracebacks import normalize_stderr

CASES = discover_cases()


def _params(cases: list[Case]) -> list[Any]:
    params = []
    for case in cases:
        marks = []
        if sys.version_info[:2] < case.min_python:
            marks.append(pytest.mark.skip(reason=f"requires Python {case.min_python}"))
        if case.xfail:
            marks.append(pytest.mark.xfail(reason=case.xfail, strict=True))
        params.append(pytest.param(case, id=case.id, marks=marks))
    return params


def _target_params(cases: list[Case]) -> list[Any]:
    params = []
    for p in _params(cases):
        case: Case = p.values[0]
        for target in (*case.targets, "noshake"):
            params.append(pytest.param(case, target, id=f"{case.id}[{target}]", marks=p.marks))
    return params


@dataclass
class Materialized:
    root: Path
    src: Path
    original: RunResult

    @property
    def entry(self) -> Path:
        return self.src / self._entry

    _entry: str = "main.py"


_CACHE: dict[str, Materialized] = {}


@pytest.fixture
def materialize(tmp_path_factory: pytest.TempPathFactory):
    def get(case: Case) -> Materialized:
        if case.id not in _CACHE:
            root = Path(os.path.realpath(tmp_path_factory.mktemp(case.id.replace("/", "_"))))
            src = write_project(root / "src", case.files, dedent=False)
            original = run_python(src / case.entry, cwd=root / "cwd-original", argv=case.argv)
            _CACHE[case.id] = Materialized(root, src, original, case.entry)
        return _CACHE[case.id]

    return get


def _options(case: Case, **overrides: Any):
    from shakeout.api import Options

    return Options(**{**case.options, **overrides})


def _sorted(items: Any) -> list[str]:
    return sorted(items)


@pytest.mark.parametrize("case", _params(CASES))
def test_original(case: Case, materialize) -> None:
    m = materialize(case)
    assert m.original.returncode == case.exit_code, m.original.describe()
    if case.stdout is not None:
        assert m.original.stdout == case.stdout, m.original.describe()


@pytest.mark.parametrize("case", _params(CASES))
def test_expectations(case: Case, materialize) -> None:
    from shakeout.api import analyze

    m = materialize(case)
    result = analyze(m.entry, _options(case))
    problems: list[str] = []
    if missing := case.removed - result.removed:
        problems.append(f"expected removed but kept/unknown: {_sorted(missing)}")
    if missing := case.kept - result.kept:
        problems.append(f"expected kept but removed/unknown: {_sorted(missing)}")
    if missing := case.dropped_modules - result.dropped_modules:
        problems.append(f"expected dropped modules: {_sorted(missing)}")
    if missing := case.bundled_modules - result.bundled_modules:
        problems.append(f"expected bundled modules: {_sorted(missing)}")
    messages = [d.message for d in result.diagnostics]
    for want in case.warnings:
        if not any(want in msg for msg in messages):
            problems.append(f"expected a diagnostic containing {want!r}; got {messages}")
    if problems:
        for sym in sorted(case.removed - result.removed):
            if sym in result.kept:
                problems.append(f"why {sym}: " + " <- ".join(result.why(sym)))
        pytest.fail("\n".join(problems) + "\n\n" + _report(result))


def _report(result: Any) -> str:
    from shakeout.api import render_report

    return render_report(result)


@pytest.mark.parametrize(("case", "target"), _target_params(CASES))
def test_equivalence(case: Case, target: str, materialize) -> None:
    from shakeout.api import build

    m = materialize(case)
    out = m.root / f"out-{target}"
    shake = target != "noshake"
    built = build(
        m.entry, out, target="single" if target == "noshake" else target,
        options=_options(case, shake=shake),
    )
    bundled = run_python(built.run_path, cwd=m.root / f"cwd-{target}", argv=case.argv)
    if case.equivalent if shake else case.noshake_equivalent:
        assert bundled.returncode == m.original.returncode, bundled.describe()
        assert bundled.stdout == m.original.stdout, bundled.describe()
        want = normalize_stderr(m.original.stderr, strip_prefixes=[str(m.src)])
        got = normalize_stderr(
            bundled.stderr, strip_prefixes=[str(out)], runtime_files=built.runtime_files
        )
        assert got == want, f"traceback/stderr mismatch\n--- original ---\n{want}\n--- bundle ---\n{got}"
    else:
        if case.bundle_exit_code is not None:
            assert bundled.returncode == case.bundle_exit_code, bundled.describe()
        for want in case.bundle_stderr_contains:
            assert want in bundled.stderr, bundled.describe()


@pytest.mark.parametrize("case", _params([c for c in CASES if c.soundness]))
def test_soundness(case: Case, materialize) -> None:
    from shakeout.api import analyze

    m = materialize(case)
    result = analyze(m.entry, _options(case))
    traced, records = trace_execution(m.entry, cwd=m.root / "cwd-trace", argv=case.argv)
    assert traced.returncode == m.original.returncode, traced.describe()
    assert records, "tracer recorded nothing"
    violations = check_soundness(records, list(result.statements))
    assert not violations, "unsound removals:\n" + "\n".join(map(str, violations))


@pytest.mark.parametrize("case", _params(CASES))
def test_golden(case: Case, materialize, update_goldens: bool) -> None:
    from shakeout.api import analyze, render_report

    m = materialize(case)
    report = render_report(analyze(m.entry, _options(case)))
    if update_goldens:
        case.golden_path.write_text(report, encoding="utf-8")
        return
    assert case.golden_path.exists(), f"missing golden; run pytest --update-goldens and review {case.golden_path}"
    assert report == case.golden_path.read_text(encoding="utf-8")
