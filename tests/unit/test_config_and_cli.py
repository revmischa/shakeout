"""In-process tests for configuration, the CLI's error paths, uv staging failures and options."""

from __future__ import annotations

import io
import json
import shutil
import sys
from typing import TYPE_CHECKING, cast

import pytest

from shakeout import cli
from shakeout.api import Options, analyze, build
from shakeout.config import ConfigError, find_pyproject, load_config
from shakeout.resolve import ModuleFinder, environment_site_dirs, project_root, stdlib_path
from shakeout.uvstage import StagingError, stage
from tests.harness.project import write_project

if TYPE_CHECKING:
    from pathlib import Path


def test_load_config_validates_types_and_tables(tmp_path: Path) -> None:
    bad_type = write_project(tmp_path / "a", {"pyproject.toml": "[tool.shakeout]\nkeep = 'x'\n"})
    with pytest.raises(ConfigError, match="keep must be a list"):
        load_config(bad_type / "pyproject.toml")
    not_table = write_project(tmp_path / "b", {"pyproject.toml": "[tool]\nshakeout = 3\n"})
    with pytest.raises(ConfigError, match="must be a table"):
        load_config(not_table / "pyproject.toml")
    broken = write_project(tmp_path / "c", {"pyproject.toml": "[tool.shakeout\n"})
    with pytest.raises(ConfigError):
        load_config(broken / "pyproject.toml")
    assert load_config(None) == {}


def test_load_config_makes_paths_absolute(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "pyproject.toml": '[tool.shakeout]\nentry = "app/main.py"\nroots = ["src"]\n'
            'search-path = ["vendor"]\n',
        },
    )
    config = load_config(root / "pyproject.toml")
    assert config["entry"] == str(root / "app" / "main.py")
    assert config["roots"] == [str(root / "src")]
    assert config["search-path"] == [str(root / "vendor")]
    assert find_pyproject(root / "app") == root / "pyproject.toml"


def test_cli_errors_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = write_project(tmp_path / "empty", {"pyproject.toml": "[project]\nname = 'x'\n"})
    assert cli.main(["build", "--config", str(empty / "pyproject.toml")]) == 2
    assert "no entry script" in capsys.readouterr().err
    assert cli.main(["build", str(tmp_path / "missing.py")]) == 2
    assert "does not exist" in capsys.readouterr().err
    src = write_project(tmp_path / "svc", {"handler.py": "def handler(e, c):\n    return 1\n"})
    assert (
        cli.main(["build", str(src / "handler.py"), "-t", "lambda", "-o", str(tmp_path / "o")]) == 2
    )
    assert "exports" in capsys.readouterr().err


def test_cli_build_lambda_prints_handler(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write_project(tmp_path / "svc", {"handler.py": "def handler(e, c):\n    return 1\n"})
    code = cli.main(
        [
            "build",
            str(src / "handler.py"),
            "-t",
            "lambda",
            "--export",
            "handler",
            "-o",
            str(tmp_path / "o"),
            "--minify",
            "--strip-docstrings",
            "--no-tombstones",
            "--no-embed-source",
            "--no-copy-dependencies",
        ]
    )
    assert code == 0
    assert "handler: handler.handler" in capsys.readouterr().out


def test_cli_report_text_and_trace_stdin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    src = write_project(tmp_path / "src", {"main.py": "def unused():\n    pass\n\nprint(1)\n"})
    assert cli.main(["report", str(src / "main.py")]) == 0
    assert "- L1-2      def unused():" in capsys.readouterr().out
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"modules": {}, "tombstones": {}}))
    monkeypatch.setattr(
        sys, "stdin", io.StringIO("Traceback (most recent call last):\nValueError\n")
    )
    assert cli.main(["trace", "-", "--manifest", str(manifest)]) == 0
    assert capsys.readouterr().out.endswith("ValueError\n")


def test_stage_reports_missing_uv_and_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(StagingError, match=r"uv\.lock"):
        stage(tmp_path)

    def no_uv(_name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", no_uv)
    with pytest.raises(StagingError, match="not on PATH"):
        stage(tmp_path)


def test_options_reject_strings_for_lists() -> None:
    with pytest.raises(TypeError, match="keep"):
        Options(keep="pkg")  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def test_why_for_unknown_and_removed_symbols(tmp_path: Path) -> None:
    src = write_project(tmp_path, {"main.py": "def unused():\n    pass\n"})
    result = analyze(src / "main.py")
    assert result.why("nope:x") == ["nope is not a bundled module"]
    assert result.why("__main__:unused") == ["__main__:unused was removed"]


def test_finder_edge_cases(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/m.py": "",
            "data.txt": "x",
            "mod.py": "",
        },
    )
    finder = ModuleFinder([str(root)], [root], frozenset())
    assert finder.find("sys") is None  # builtin
    assert finder.find("mod.child") is None  # parent is not a package
    assert finder.find("does_not_exist") is None
    pkg = finder.find("pkg")
    assert pkg is not None
    assert finder.submodules(pkg) == ["pkg.sub", "pkg.sub.m"]
    mod = finder.find("mod")
    assert mod is not None
    assert finder.submodules(mod) == []
    outside = ModuleFinder([str(root)], [tmp_path / "elsewhere"], frozenset())
    found = outside.find("mod")
    assert found is not None
    assert not outside.bundleable(found)


def test_vendors_plain_module_without_distribution(tmp_path: Path) -> None:
    site = write_project(
        tmp_path / "site-packages", {"loose.py": "VALUE = 7\n", "loosepkg/__init__.py": "X = 1\n"}
    )
    src = write_project(
        tmp_path / "app", {"main.py": "import loose, loosepkg\nprint(loose.VALUE, loosepkg.X)\n"}
    )
    built = build(
        src / "main.py", tmp_path / "out", target="tree", options=Options(search_path=(str(site),))
    )
    deps = cast("list[str]", built.manifest["dependencies"])
    assert sorted(deps) == ["loose", "loosepkg"]
    assert (tmp_path / "out" / "loose.py").is_file()
    assert (tmp_path / "out" / "loosepkg" / "__init__.py").is_file()


def test_keep_and_export_of_undefined_names_warn(tmp_path: Path) -> None:
    src = write_project(tmp_path, {"main.py": "import lib\n", "lib.py": "def f():\n    pass\n"})
    result = analyze(src / "main.py", Options(keep=("lib:nope",)))
    assert any("nothing in the bundle defines 'nope'" in d.message for d in result.diagnostics)
    lib = analyze(src / "main.py", Options(exports=("missing",)))
    assert any("export 'missing'" in d.message for d in lib.diagnostics)


def test_keep_symbol_naming_a_submodule_keeps_it_whole(tmp_path: Path) -> None:
    src = write_project(
        tmp_path,
        {
            "main.py": "import pkg\n",
            "pkg/__init__.py": "",
            "pkg/plugin.py": "def a():\n    pass\n\n\ndef b():\n    pass\n",
        },
    )
    result = analyze(src / "main.py", Options(keep=("pkg:plugin",)))
    assert {"pkg.plugin:a", "pkg.plugin:b"} <= result.kept
    reason = result.reasons["pkg.plugin", 0]
    assert (reason.kind, reason.detail) == ("whole", "keep option pkg:plugin")


def test_environment_site_dirs_prefers_project_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    project = tmp_path / "proj"
    project_site = project / ".venv" / "lib" / pyver / "site-packages"
    active_site = tmp_path / "active" / "lib" / pyver / "site-packages"
    for d in (project_site, active_site):
        d.mkdir(parents=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "active"))
    assert environment_site_dirs(project) == [str(active_site), str(project_site)]
    # The environment shakeout itself runs in is only used when it lives inside the project.
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    assert environment_site_dirs(project) == [str(project_site)]
    # `uv run shakeout` inside the project: the active environment is the one running shakeout.
    monkeypatch.setattr(sys, "prefix", str(project / ".venv"))
    monkeypatch.setenv("VIRTUAL_ENV", str(project / ".venv"))
    own = environment_site_dirs(project)
    assert own[0] == str(project_site)
    assert all("pybund/.venv" not in d for d in own[:1])
    assert project_root(project / "src") == project / "src"
    (project / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert project_root(project / "src" / "pkg") == project


def test_stdlib_path_excludes_site_packages() -> None:
    dirs = stdlib_path()
    assert dirs
    assert not any("site-packages" in d for d in dirs)
