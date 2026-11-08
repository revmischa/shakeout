"""The documentation must describe the code that exists."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from shakeout import __version__, cli
from shakeout.config import KEYS
from tests.harness.cases import discover_cases

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DOCS = Path(__file__).resolve().parents[1] / "docs"


COMMANDS = ("build", "report", "why", "trace")


def _flags(capsys: pytest.CaptureFixture[str]) -> set[str]:
    """Long options of every subcommand, read from its public --help output."""
    flags: set[str] = set()
    for command in COMMANDS:
        with pytest.raises(SystemExit):
            cli.main([command, "--help"])
        flags.update(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", capsys.readouterr().out))
    return flags - {"--help"}


def test_every_cli_flag_is_documented(capsys: pytest.CaptureFixture[str]) -> None:
    text = (DOCS / "reference" / "cli.md").read_text()
    missing = sorted(f for f in _flags(capsys) if f"`{f}" not in text)
    assert missing == []


def test_every_config_key_is_documented() -> None:
    text = (DOCS / "reference" / "configuration.md").read_text()
    missing = sorted(k for k in KEYS if f"| `{k}` |" not in text)
    assert missing == []


def test_case_count_claims_are_current() -> None:
    count = len(discover_cases())
    for path in (DOCS / "concepts" / "soundness.md", Path(__file__).parents[1] / "README.md"):
        claims = {
            int(n) for n in re.findall(r"(\d+) (?:multi-file programs|cases)", path.read_text())
        }
        assert claims <= {count}, f"{path.name} claims {claims}, catalog has {count}"


def test_version_is_consistent() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["version"] == __version__
    assert f"## {__version__} " in (root / "CHANGELOG.md").read_text()
