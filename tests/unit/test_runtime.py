"""The embedded runtime loader, exercised directly (outside a generated bundle)."""

from __future__ import annotations

import linecache
import sys
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from shakeout.api import build
from tests.harness.project import write_project
from tests.harness.run import run_python

if TYPE_CHECKING:
    from types import ModuleType

RUNTIME = Path(__file__).resolve().parents[2] / "src" / "shakeout" / "runtime.py"


Bootstrap = Callable[..., None]


@pytest.fixture
def bootstrap() -> Iterator[Bootstrap]:
    ns: dict[str, object] = {}
    exec(compile(RUNTIME.read_text(), str(RUNTIME), "exec"), ns)  # noqa: S102 - loads it
    before_meta = list(sys.meta_path)
    before_modules = set(sys.modules)
    yield cast("Bootstrap", ns["_shakeout_bootstrap"])
    sys.meta_path[:] = before_meta
    for name in set(sys.modules) - before_modules:
        del sys.modules[name]


def test_serves_packages_and_preserves_lines(bootstrap: Bootstrap) -> None:
    modules = {
        "rt_pkg": ("rt_pkg/__init__.py", True, ((1, "X = 1\n"),)),
        "rt_pkg.sub": ("rt_pkg/sub.py", False, ((5, "def boom():\n    raise ValueError('x')\n"),)),
    }
    entry = ("main.py", ((3, "import rt_pkg.sub\nresult = rt_pkg.sub\n"),))
    g: dict[str, object] = {"__name__": "__rt_test__", "_shakeout_x": 1}
    bootstrap(modules, entry, {}, g)
    assert "_shakeout_x" not in g
    sub = cast("ModuleType", g["result"])
    assert sub.__name__ == "rt_pkg.sub"
    assert sub.__package__ == "rt_pkg"
    assert sys.modules["rt_pkg"].__path__ == []
    assert sub.__file__ == "rt_pkg/sub.py"
    try:
        sub.boom()
    except ValueError as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        assert (frame.filename, frame.lineno, frame.line) == (
            "rt_pkg/sub.py",
            6,
            "raise ValueError('x')",
        )
    assert linecache.getline("rt_pkg/sub.py", 5) == "def boom():\n"


def test_module_not_found_gets_dynamic_import_note(bootstrap: Bootstrap) -> None:
    entry = ("main.py", ((1, "import rt_missing_thing\n"),))
    meta = {"dynamic_imports": ["main.py:9"]}
    with pytest.raises(ModuleNotFoundError) as info:
        bootstrap({}, entry, meta, {"__name__": "__rt__"})
    notes = getattr(info.value, "__notes__", [])
    if sys.version_info >= (3, 11):
        assert any("main.py:9" in n and "keep" in n for n in notes)


def test_dropped_module_note(bootstrap: Bootstrap) -> None:
    entry = ("main.py", ((1, "import rt_dropped\n"),))
    with pytest.raises(ModuleNotFoundError) as info:
        bootstrap({}, entry, {"dropped": ["rt_dropped"]}, {"__name__": "__rt__"})
    if sys.version_info >= (3, 11):
        assert "removed from this bundle" in info.value.__notes__[0]


def test_name_error_tombstone_note(bootstrap: Bootstrap) -> None:
    entry = ("main.py", ((1, "gone()\n"),))
    meta = {"tombstones": {"lib": {"gone": ["lib.py", 4]}}}
    with pytest.raises(NameError) as info:
        bootstrap({}, entry, meta, {"__name__": "__rt__"})
    if sys.version_info >= (3, 11):
        assert "lib.py:4" in info.value.__notes__[0]
        assert "keep = ['lib:gone']" in info.value.__notes__[0]


def test_system_exit_passes_through_untouched(bootstrap: Bootstrap) -> None:
    entry = ("main.py", ((1, "raise SystemExit(4)\n"),))
    with pytest.raises(SystemExit) as info:
        bootstrap({}, entry, {}, {"__name__": "__rt__"})
    assert info.value.code == 4
    assert not getattr(info.value, "__notes__", None)


def test_runtime_defines_only_prefixed_names() -> None:
    ns: dict[str, object] = {}
    exec(compile(RUNTIME.read_text(), str(RUNTIME), "exec"), ns)  # noqa: S102 - loads it
    public = {k for k in ns if not k.startswith("__")}
    assert public - {"annotations", "TYPE_CHECKING"} == {
        k for k in public if k.lower().startswith("_shakeout")
    }


def test_bundle_globals_are_clean(tmp_path: Path) -> None:
    src = write_project(
        tmp_path / "src",
        {"main.py": "print(sorted(k for k in globals() if not k.startswith('__')))\n"},
    )
    built = build(src / "main.py", tmp_path / "out")
    assert run_python(built.run_path, cwd=tmp_path).stdout == "[]\n"
