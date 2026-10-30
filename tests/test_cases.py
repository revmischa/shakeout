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
from typing import TYPE_CHECKING, Any, Protocol

import pytest

from shakeout.api import Options, ShakeResult, analyze, build, render_report
from tests.harness.cases import Case, discover_cases
from tests.harness.project import write_project
from tests.harness.run import RunResult, run_python
from tests.harness.soundness import check_soundness, trace_execution
from tests.harness.tracebacks import normalize_stderr, structure

if TYPE_CHECKING:
    from _pytest.mark.structures import ParameterSet

CASES = discover_cases()
_NO_SHAKE = "noshake"


def _marks(case: Case) -> list[pytest.MarkDecorator]:
    marks: list[pytest.MarkDecorator] = []
    if sys.version_info[:2] < case.min_python:
        marks.append(pytest.mark.skip(reason=f"requires Python {case.min_python}"))
    if case.xfail:
        marks.append(pytest.mark.xfail(reason=case.xfail, strict=True))
    return marks


def _params(cases: list[Case]) -> list[ParameterSet]:
    return [pytest.param(case, id=case.id, marks=_marks(case)) for case in cases]


def _target_params(cases: list[Case]) -> list[ParameterSet]:
    return [
        pytest.param(case, target, id=f"{case.id}[{target}]", marks=_marks(case))
        for case in cases
        for target in (*case.targets, _NO_SHAKE)
    ]


@dataclass
class Materialized:
    """A case written to disk, with its original run and (lazily) its analysis."""

    case: Case
    root: Path
    src: Path
    original: RunResult
    _result: ShakeResult | None = None

    @property
    def entry(self) -> Path:
        return self.src / self.case.entry

    @property
    def result(self) -> ShakeResult:
        # Under mutation testing, analyses must run inside each mutant's process: a result
        # cached before mutmut forks would hide every mutant.
        if self._result is None or os.environ.get("MUTANT_UNDER_TEST"):
            self._result = analyze(self.entry, _options(self.case))
        return self._result


class Materializer(Protocol):
    def __call__(self, case: Case) -> Materialized: ...


_CACHE: dict[str, Materialized] = {}


@pytest.fixture
def materialize(tmp_path_factory: pytest.TempPathFactory) -> Materializer:
    def get(case: Case) -> Materialized:
        if case.id not in _CACHE:
            root = Path(os.path.realpath(tmp_path_factory.mktemp(case.id.replace("/", "_"))))
            src = write_project(root / "src", case.files, dedent=False)
            original = run_python(src / case.entry, cwd=root / "cwd-original", argv=case.argv)
            _CACHE[case.id] = Materialized(case, root, src, original)
        return _CACHE[case.id]

    return get


def _options(case: Case, *, shake: bool = True, minify: bool = False) -> Options:
    settings: dict[str, Any] = {**case.options, "shake": shake, "minify": minify}
    return Options(**settings)


@pytest.mark.parametrize("case", _params(CASES))
def test_original(case: Case, materialize: Materializer) -> None:
    m = materialize(case)
    assert m.original.returncode == case.exit_code, m.original.describe()
    if case.stdout is not None:
        assert m.original.stdout == case.stdout, m.original.describe()


def _expectation_problems(case: Case, result: ShakeResult) -> list[str]:
    checks = [
        ("expected removed but kept/unknown", case.removed - result.removed),
        ("expected kept but removed/unknown", case.kept - result.kept),
        ("expected dropped modules", case.dropped_modules - result.dropped_modules),
        ("expected bundled modules", case.bundled_modules - result.bundled_modules),
    ]
    problems = [f"{label}: {sorted(missing)}" for label, missing in checks if missing]
    messages = [d.message for d in result.diagnostics]
    problems.extend(
        f"expected a diagnostic containing {want!r}; got {messages}"
        for want in case.warnings
        if not any(want in msg for msg in messages)
    )
    problems.extend(
        f"why {sym}: " + " <- ".join(result.why(sym))
        for sym in sorted(case.removed - result.removed)
        if sym in result.kept
    )
    return problems


@pytest.mark.parametrize("case", _params(CASES))
def test_expectations(case: Case, materialize: Materializer) -> None:
    result = materialize(case).result
    problems = _expectation_problems(case, result)
    if problems:
        pytest.fail("\n".join(problems) + "\n\n" + render_report(result))


@pytest.mark.parametrize(("case", "target"), _target_params(CASES))
def test_equivalence(case: Case, target: str, materialize: Materializer) -> None:
    m = materialize(case)
    out = m.root / f"out-{target}"
    shake = target != _NO_SHAKE
    built = build(
        m.entry,
        out,
        target="single" if target in (_NO_SHAKE, "minify") else target,
        options=_options(case, shake=shake, minify=target == "minify"),
    )
    bundled = run_python(built.run_path, cwd=m.root / f"cwd-{target}", argv=case.argv)
    if not (case.equivalent if shake else case.noshake_equivalent):
        if case.bundle_exit_code is not None:
            assert bundled.returncode == case.bundle_exit_code, bundled.describe()
        for want in case.bundle_stderr_contains:
            assert want in bundled.stderr, bundled.describe()
        return
    assert bundled.returncode == m.original.returncode, bundled.describe()
    assert bundled.stdout == m.original.stdout, bundled.describe()
    want = normalize_stderr(m.original.stderr, strip_prefixes=[str(m.src)])
    got = normalize_stderr(
        bundled.stderr, strip_prefixes=[str(out)], runtime_files=built.runtime_files
    )
    if target == "minify":  # displayed source lines lose comments; frames must not change
        want, got = structure(want), structure(got)
    if target == "pyz":  # zipimport shows paths inside the archive
        got = got.replace(f"{built.run_path.name}/", "")
    assert got == want, f"stderr mismatch\n--- original ---\n{want}\n--- bundle ---\n{got}"


@pytest.mark.parametrize("case", _params([c for c in CASES if c.soundness]))
def test_soundness(case: Case, materialize: Materializer) -> None:
    m = materialize(case)
    traced, records = trace_execution(m.entry, cwd=m.root / "cwd-trace", argv=case.argv)
    assert traced.returncode == m.original.returncode, traced.describe()
    assert records, "tracer recorded nothing"
    violations = check_soundness(records, list(m.result.statements))
    assert not violations, "unsound removals:\n" + "\n".join(map(str, violations))


@pytest.mark.parametrize("case", _params(CASES))
def test_golden(case: Case, materialize: Materializer, *, update_goldens: bool) -> None:
    report = render_report(materialize(case).result)
    if update_goldens:
        case.golden_path.write_text(report, encoding="utf-8")
        return
    assert case.golden_path.exists(), (
        f"missing golden; run pytest --update-goldens and review {case.golden_path}"
    )
    assert report == case.golden_path.read_text(encoding="utf-8")
