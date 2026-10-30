"""Property tests for emitters: line preservation, determinism, idempotence, monotonicity."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shakeout.api import Options, analyze, build
from shakeout.emit import build_source
from tests.harness.project import write_project

from .helpers import shake

LIB = '''\
"""lib docs."""
import os

A = 1  # comment for A


def f():
    return A


# a comment block
# for g
def g(x=os.sep):
    return x


class C:
    def m(self):
        return f()


B = [A,
     2]
'''


@settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(st.lists(st.sampled_from(["f", "g", "C", "B", "A"]), max_size=5, unique=True))
def test_kept_statements_keep_original_line_numbers(tmp_path: Path, used: list[str]) -> None:
    main = "import lib\n" + "".join(f"print(lib.{u})\n" for u in used)
    result = shake(tmp_path / "-".join(used or ["none"]), {"main.py": main, "lib.py": LIB})
    mod = result.modules["lib"]
    from shakeout.emit import module_chunks

    emitted = build_source(module_chunks(result, mod)).splitlines()
    original = LIB.splitlines()
    for stmt in mod.statements:
        if stmt.kept:
            for line in range(stmt.start, stmt.end + 1):
                assert emitted[line - 1] == original[line - 1]
    ast.parse("\n".join(emitted))  # still valid Python


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(path).as_posix().encode() + p.read_bytes())
    return h.hexdigest()


PROJECT = {
    "main.py": "from pkg import api\nimport plugins\nprint(api.run())\n",
    "pkg/__init__.py": "from .core import run as _r\n",
    "pkg/api.py": "from .core import run\nfrom .util import unused\n",
    "pkg/core.py": "def run():\n    return helper()\n\ndef helper():\n    return 1\n\ndef dead():\n    pass\n",
    "pkg/util.py": "def unused():\n    pass\n",
    "plugins.py": "REG = []\ndef reg(f):\n    REG.append(f)\n    return f\n@reg\ndef p(): pass\n",
}


def test_build_is_deterministic(tmp_path: Path) -> None:
    src = write_project(tmp_path / "src", PROJECT)
    digests = set()
    for i in range(3):
        for target in ("single", "tree"):
            out = tmp_path / f"out-{target}-{i}"
            build(src / "main.py", out, target=target)
            digests.add((target, _digest(out)))
    assert len(digests) == 2


def test_shaking_is_idempotent(tmp_path: Path) -> None:
    src = write_project(tmp_path / "src", PROJECT)
    first = build(src / "main.py", tmp_path / "t1", target="tree")
    second = build(first.run_path, tmp_path / "t2", target="tree")
    assert second.result.removed - {s for s in second.result.removed if s.endswith(":__getattr__")} == set()
    a = {p.relative_to(tmp_path / "t1").as_posix(): p.read_text() for p in (tmp_path / "t1").rglob("*.py")}
    b = {p.relative_to(tmp_path / "t2").as_posix(): p.read_text() for p in (tmp_path / "t2").rglob("*.py")}
    assert a.keys() == b.keys()
    for name in a:
        assert a[name].rstrip("\n") == b[name].rstrip("\n") or "__getattr__" in a[name], name


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(st.lists(st.sampled_from(["pkg.core:dead", "pkg.util:unused", "pkg.core:helper", "pkg.api",
                                 "plugins:REG"]), max_size=4, unique=True))
def test_more_keeps_never_remove_more(tmp_path: Path, keeps: list[str]) -> None:
    src = write_project(tmp_path / "src", PROJECT)
    base = analyze(src / "main.py")
    more = analyze(src / "main.py", Options(keep=tuple(keeps)))
    assert {k for k in base.live} <= {k for k in more.live}


def test_assume_pure_bases_only_removes(tmp_path: Path) -> None:
    files = {"main.py": "import lib\n", "lib.py": "import socketserver\nclass H(socketserver.BaseRequestHandler): pass\n"}
    src = write_project(tmp_path / "src", files)
    strict = analyze(src / "main.py")
    loose = analyze(src / "main.py", Options(assume_pure_bases=True))
    assert loose.live <= strict.live
