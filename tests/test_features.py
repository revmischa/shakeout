"""End-to-end tests for library entries, Lambda/pyz archives, vendoring, uv staging, minify and
trace."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from shakeout.api import ArchiveError, Options, build
from tests.harness.project import write_project
from tests.harness.run import run_python
from tests.harness.tracebacks import normalize_stderr

HANDLER = {
    "handler.py": '''\
        import json

        from service import greet


        def handler(event, context):
            if event.get("fail"):
                return greet(None)
            return {"statusCode": 200, "body": json.dumps(greet(event["name"]))}


        def unused_helper():
            return "never"


        if __name__ == "__main__":
            print("local run")
        ''',
    "service.py": '''\
        def greet(name):
            return "hello " + name.upper()


        def farewell(name):
            return "bye " + name
        ''',
}

# Mimics how the AWS Lambda Python runtime imports a handler and reports an error.
INVOKE = '''\
import importlib, json, sys, traceback
sys.path.insert(0, sys.argv[1])
module, _, func = sys.argv[2].rpartition(".")
event = json.loads(sys.argv[3])
try:
    print(json.dumps(getattr(importlib.import_module(module), func)(event, None)))
except Exception as exc:
    frames = [[f.filename.rpartition(sys.argv[1] + "/")[2], f.lineno, f.name, f.line]
              for f in traceback.extract_tb(exc.__traceback__)]
    print(json.dumps({"errorType": type(exc).__name__, "stackTrace": frames}))
'''


def invoke(root: Path, task_root: Path, handler: str, event: dict[str, object]) -> object:
    script = root / "invoke.py"
    script.write_text(INVOKE)
    result = run_python(script, cwd=root, argv=(str(task_root), handler, json.dumps(event)))
    assert result.returncode == 0, result.describe()
    return json.loads(result.stdout)


@pytest.fixture
def service(tmp_path: Path) -> Path:
    return write_project(Path(os.path.realpath(tmp_path)) / "src", HANDLER)


def test_library_entry_single_file_is_importable(service: Path, tmp_path: Path) -> None:
    built = build(service / "handler.py", tmp_path / "out", options=Options(exports=("handler",)))
    text = built.run_path.read_text().split("_shakeout_meta =")[0]
    assert "def unused_helper" not in text
    assert "local run" not in text
    assert "def farewell" not in text
    event = {"name": "ada"}
    expected = invoke(tmp_path, service, "handler.handler", event)
    assert invoke(tmp_path, built.out, "handler.handler", event) == expected


def test_lambda_package_matches_original_including_stack_traces(service: Path, tmp_path: Path) -> None:
    built = build(service / "handler.py", tmp_path / "out", target="lambda",
                  options=Options(exports=("handler",)))
    assert built.run_path.suffix == ".zip"
    assert built.manifest["handlers"] == ["handler.handler"]
    task_root = tmp_path / "task"
    with zipfile.ZipFile(built.run_path) as zf:
        zf.extractall(task_root)
        names = set(zf.namelist())
    assert {"handler.py", "service.py", "shakeout-manifest.json"} <= names
    for event in ({"name": "ada"}, {"fail": True}):
        expected = invoke(tmp_path, service, "handler.handler", event)
        assert invoke(tmp_path, task_root, "handler.handler", event) == expected
    error = invoke(tmp_path, task_root, "handler.handler", {"fail": True})
    assert error["stackTrace"][-1] == ["service.py", 2, "greet", 'return "hello " + name.upper()']  # type: ignore[index]


def test_lambda_zip_is_reproducible(service: Path, tmp_path: Path) -> None:
    opts = Options(exports=("handler",))
    a = build(service / "handler.py", tmp_path / "a", target="lambda", options=opts)
    b = build(service / "handler.py", tmp_path / "b", target="lambda", options=opts)
    assert a.run_path.read_bytes() == b.run_path.read_bytes()


def test_archive_targets_validate_entry_kind(service: Path, tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="exports"):
        build(service / "handler.py", tmp_path / "x", target="lambda")
    with pytest.raises(ArchiveError, match="pyz"):
        build(service / "handler.py", tmp_path / "y", target="pyz", options=Options(exports=("handler",)))


def _fake_distribution(site: Path) -> None:
    write_project(site, {
        "thirdpkg/__init__.py": "from importlib.resources import files\n\n\ndef data():\n    return files(__name__).joinpath('data.txt').read_text().strip()\n",
        "thirdpkg/data.txt": "payload",
        "thirdpkg/_speedups.py": "FAST = True\n",
        "thirdpkg-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: thirdpkg\nVersion: 1.0\n",
        "thirdpkg-1.0.dist-info/top_level.txt": "thirdpkg\n",
        "thirdpkg-1.0.dist-info/RECORD": "thirdpkg/__init__.py,,\nthirdpkg/data.txt,,\nthirdpkg/_speedups.py,,\nthirdpkg-1.0.dist-info/METADATA,,\nthirdpkg-1.0.dist-info/top_level.txt,,\nthirdpkg-1.0.dist-info/RECORD,,\n",
    })


def test_tree_vendors_whole_distributions_with_data_and_metadata(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    _fake_distribution(site)
    src = write_project(tmp_path / "app", {
        "main.py": "import importlib.metadata\nimport thirdpkg\nprint(thirdpkg.data(), importlib.metadata.version('thirdpkg'))\n",
    })
    built = build(src / "main.py", tmp_path / "out", target="tree", options=Options(search_path=(str(site),)))
    assert built.manifest["dependencies"] == ["thirdpkg"]
    assert (tmp_path / "out" / "thirdpkg" / "data.txt").is_file()
    assert run_python(built.run_path, cwd=tmp_path).stdout == "payload 1.0\n"


def test_included_package_is_shaken_and_keeps_metadata(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    _fake_distribution(site)
    src = write_project(tmp_path / "app", {"main.py": "import thirdpkg._speedups as s\nprint(s.FAST)\n"})
    built = build(src / "main.py", tmp_path / "out", target="tree",
                  options=Options(search_path=(str(site),), include=("thirdpkg",)))
    assert "thirdpkg._speedups" in built.result.bundled_modules
    assert (tmp_path / "out" / "thirdpkg-1.0.dist-info" / "METADATA").is_file()
    assert not (tmp_path / "out" / "thirdpkg" / "data.txt").exists()  # shaken: not vendored
    assert run_python(built.run_path, cwd=tmp_path).stdout == "True\n"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_uv_staging_uses_the_lockfile(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    write_project(root / "dep", {
        "pyproject.toml": '[project]\nname = "dep"\nversion = "0.1"\n[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n',
        "src/dep/__init__.py": "VALUE = 'from dep'\n",
    })
    app = write_project(root / "app", {
        "pyproject.toml": '[project]\nname = "app"\nversion = "0.1"\nrequires-python = ">=3.10"\ndependencies = ["dep"]\n[tool.uv.sources]\ndep = { path = "../dep" }\n[tool.shakeout]\nentry = "main.py"\ntarget = "tree"\nout = "dist"\nuv = true\n',
        "main.py": "import dep\nprint(dep.VALUE)\n",
    })
    subprocess.run(["uv", "lock", "--quiet"], cwd=app, check=True)  # noqa: S607
    proc = subprocess.run([sys.executable, "-m", "shakeout", "build"], cwd=app, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert (app / "dist" / "dep" / "__init__.py").is_file()
    assert run_python(app / "dist" / "main.py", cwd=root).stdout == "from dep\n"


def test_no_embedded_source_then_trace_restores_lines(tmp_path: Path) -> None:
    src = write_project(tmp_path / "src", {
        "main.py": "from lib import fail\n\nfail()\n",
        "lib.py": "def unused():\n    pass\n\n\ndef fail():\n    raise ValueError('boom')\n",
    })
    original = run_python(src / "main.py", cwd=tmp_path)
    built = build(src / "main.py", tmp_path / "out", options=Options(embed_source=False))
    bare = run_python(built.run_path, cwd=tmp_path / "elsewhere")
    assert "raise ValueError" not in bare.stderr
    trace_input = tmp_path / "tb.txt"
    trace_input.write_text(bare.stderr)
    proc = subprocess.run(
        [sys.executable, "-m", "shakeout", "trace", str(trace_input), "--manifest",
         str(tmp_path / "out" / "main.manifest.json"), "--root", str(src)],
        capture_output=True, text=True, check=True,
    )
    want = normalize_stderr(original.stderr, strip_prefixes=[str(src)])
    got = normalize_stderr(proc.stdout, strip_prefixes=[str(tmp_path / "out")],
                           runtime_files=built.runtime_files)
    strip_carets = lambda t: "\n".join(l for l in t.splitlines() if not set(l.strip()) <= set("~^"))  # noqa: E731
    assert strip_carets(got) == strip_carets(want)


def test_strip_docstrings_behaves_like_dash_OO(tmp_path: Path) -> None:
    src = write_project(tmp_path / "src", {
        "main.py": '"""Entry."""\nfrom lib import f, C\n\nprint(f(), C().m(), f.__doc__, C.__doc__, __doc__)\n',
        "lib.py": 'def f():\n    """Only a docstring."""\n\n\nclass C:\n    """Doc."""\n\n    def m(self):\n        """Doc.\n\n        More.\n        """\n        return 2\n',
    })
    built = build(src / "main.py", tmp_path / "out", options=Options(strip_docstrings=True, minify=True))
    assert run_python(built.run_path, cwd=tmp_path).stdout == "None 2 None None None\n"
    assert "Doc." not in built.run_path.read_text().split("_shakeout_modules")[1]


def test_trace_restores_original_lines_of_a_minified_bundle(tmp_path: Path) -> None:
    src = write_project(tmp_path / "src", {
        "main.py": "from lib import fail\n\nfail()  # call it\n",
        "lib.py": "def fail():\n    raise ValueError('boom')  # explain\n",
    })
    built = build(src / "main.py", tmp_path / "out", options=Options(minify=True))
    bare = run_python(built.run_path, cwd=tmp_path / "elsewhere")
    assert "# explain" not in bare.stderr and "raise ValueError('boom')" in bare.stderr
    tb = tmp_path / "tb.txt"
    tb.write_text(bare.stderr)
    proc = subprocess.run(
        [sys.executable, "-m", "shakeout", "trace", str(tb), "--manifest",
         str(tmp_path / "out" / "main.manifest.json"), "--root", str(src)],
        capture_output=True, text=True, check=True,
    )
    assert "raise ValueError('boom')  # explain" in proc.stdout
    assert "fail()  # call it" in proc.stdout
