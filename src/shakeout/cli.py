"""Command-line interface: ``shakeout build | report | why | trace``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from shakeout import __version__
from shakeout.api import TARGETS, ArchiveError, BuildError, Options, analyze, build, render_report
from shakeout.config import ConfigError, find_pyproject, load_config
from shakeout.trace import symbolicate
from shakeout.uvstage import StagingError, stage

_ERRORS = (BuildError, ConfigError, ArchiveError, StagingError, OSError)


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("entry", nargs="?", help="entry script (default: [tool.shakeout] entry)")
    add = common.add_argument
    add("--keep", action="append", default=[], metavar="MOD[:NAME]",
        help="always keep a module or symbol (repeatable)")
    add("--include", action="append", default=[], metavar="PKG",
        help="bundle and shake this third-party package; '*' for all (repeatable)")
    add("--export", action="append", default=[], metavar="NAME",
        help="entry function called from outside, e.g. a Lambda handler (repeatable)")
    add("--pure-call", action="append", default=[], metavar="QUALNAME",
        help="treat calls to this function as side-effect free (repeatable)")
    add("--assume-pure-bases", action="store_true", default=None,
        help="assume unknown base classes do not register subclasses")
    add("--root", action="append", default=[], metavar="DIR",
        help="extra first-party source root (repeatable)")
    add("--search-path", action="append", default=[], metavar="DIR",
        help="extra directory with third-party packages (repeatable)")
    add("--uv", action="store_true", default=None,
        help="install the locked dependencies (uv.lock) for the target and use them")
    add("--python-version", metavar="X.Y", help="target Python for --uv (default: current)")
    add("--platform", metavar="TRIPLE", help="target platform for --uv, e.g. x86_64-manylinux_2_28")
    add("--no-shake", action="store_true", help="bundle without removing code")
    add("--config", type=Path, help="pyproject.toml to read (default: search upwards)")
    return common


def _parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(prog="shakeout", description="Tree-shaking bundler for Python.")
    parser.add_argument("--version", action="version", version=f"shakeout {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", parents=[common], help="write a bundle")
    b.add_argument("-o", "--out", type=Path, help="output directory (default: dist/)")
    b.add_argument("-t", "--target", choices=TARGETS, help="artifact type (default: single)")
    b.add_argument("--minify", action="store_true", default=None,
                   help="drop comments and blank lines (line numbers stay exact)")
    b.add_argument("--strip-docstrings", action="store_true", default=None,
                   help="also drop docstrings, like python -OO")
    b.add_argument("--no-embed-source", action="store_true",
                   help="do not ship sources for traceback lines (see 'shakeout trace')")
    b.add_argument("--no-tombstones", action="store_true",
                   help="do not explain removed names when they are accessed at runtime")
    b.add_argument("--no-copy-dependencies", action="store_true",
                   help="tree/pyz/lambda: do not ship third-party packages that are not shaken")
    r = sub.add_parser("report", parents=[common], help="show what is kept and why")
    r.add_argument("--json", action="store_true", help="machine-readable output")
    w = sub.add_parser("why", parents=[common], help="explain why a symbol is kept")
    w.add_argument("symbol", help="module:name, e.g. pkg.core:helper")
    t = sub.add_parser("trace", help="add source lines and removal notes to a traceback")
    t.add_argument("traceback", type=Path, help="file with the traceback text ('-' for stdin)")
    t.add_argument("--manifest", type=Path, required=True, help="shakeout-manifest.json")
    t.add_argument("--root", type=Path, default=Path(), help="original source root (default: .)")
    return parser


def _flag(value: bool | None, config: dict[str, Any], key: str, *, default: bool) -> bool:  # noqa: FBT001
    return bool(value if value is not None else config.get(key, default))


def _options(args: argparse.Namespace, config: dict[str, Any]) -> Options:
    search = [*config.get("search-path", ()), *args.search_path]
    if _flag(args.uv, config, "uv", default=False):
        project = Path(config.get("_project", Path(args.entry or ".").resolve().parent))
        search.insert(0, str(stage(
            project,
            python_version=args.python_version or config.get("python-version"),
            platform=args.platform or config.get("platform"),
        )))
    return Options(
        keep=(*config.get("keep", ()), *args.keep),
        include=(*config.get("include", ()), *args.include),
        exports=(*config.get("exports", ()), *args.export),
        pure_calls=(*config.get("pure-calls", ()), *args.pure_call),
        assume_pure_bases=_flag(args.assume_pure_bases, config, "assume-pure-bases", default=False),
        roots=(*config.get("roots", ()), *args.root),
        search_path=tuple(search),
        shake=not args.no_shake,
        tombstones=not getattr(args, "no_tombstones", False) and config.get("tombstones", True),
        embed_source=not getattr(args, "no_embed_source", False)
        and config.get("embed-source", True),
        minify=_flag(getattr(args, "minify", None), config, "minify", default=False),
        strip_docstrings=_flag(getattr(args, "strip_docstrings", None), config,
                               "strip-docstrings", default=False),
        copy_dependencies=not getattr(args, "no_copy_dependencies", False)
        and config.get("copy-dependencies", True),
    )


def _entry(args: argparse.Namespace, config: dict[str, Any]) -> Path:
    entry = args.entry or config.get("entry")
    if not entry:
        msg = "no entry script given (pass one or set [tool.shakeout] entry)"
        raise ConfigError(msg)
    path = Path(entry)
    if not path.is_file():
        msg = f"entry script {path} does not exist"
        raise ConfigError(msg)
    return path


def _cmd_build(args: argparse.Namespace, config: dict[str, Any]) -> int:
    target = args.target or config.get("target", "single")
    out = args.out or Path(config.get("out", "dist"))
    built = build(_entry(args, config), out, target=target, options=_options(args, config))
    res = built.result
    for diag in res.diagnostics:
        if diag.level != "info":
            print(diag, file=sys.stderr)
    kept = sum(st.kept for st in res.statements)
    print(
        f"wrote {built.run_path} ({target}, build {built.manifest['build_id']}): "
        f"{len(res.loaded)} modules, {kept}/{len(res.statements)} top-level statements kept, "
        f"{len(res.dropped_modules)} modules dropped"
    )
    for handler in built.manifest.get("handlers", ()):  # type: ignore[union-attr]
        print(f"handler: {handler}")
    return 0


def _cmd_report(args: argparse.Namespace, config: dict[str, Any]) -> int:
    result = analyze(_entry(args, config), _options(args, config))
    if not args.json:
        print(render_report(result), end="")
        return 0
    data = {
        "bundled": sorted(result.bundled_modules),
        "dropped": sorted(result.dropped_modules),
        "kept": sorted(result.kept),
        "removed": sorted(result.removed),
        "diagnostics": [vars(d) for d in result.diagnostics],
    }
    print(json.dumps(data, indent=2))
    return 0


def _cmd_why(args: argparse.Namespace, config: dict[str, Any]) -> int:
    result = analyze(_entry(args, config), _options(args, config))
    for i, step in enumerate(result.why(args.symbol)):
        print(("  " * i) + ("<- " if i else "") + step)
    return 0 if args.symbol in result.kept else 1


def _cmd_trace(args: argparse.Namespace, _config: dict[str, Any]) -> int:
    text = sys.stdin.read() if str(args.traceback) == "-" else args.traceback.read_text("utf-8")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    print(symbolicate(text, manifest, args.root), end="")
    return 0


_COMMANDS = {"build": _cmd_build, "report": _cmd_report, "why": _cmd_why, "trace": _cmd_trace}


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = _parser().parse_args(argv)
    try:
        config = {} if args.command == "trace" else _load(args)
        return _COMMANDS[args.command](args, config)
    except _ERRORS as exc:
        print(f"shakeout: error: {exc}", file=sys.stderr)
        return 2


def _load(args: argparse.Namespace) -> dict[str, Any]:
    start = Path(args.entry).resolve().parent if args.entry else Path.cwd()
    return load_config(args.config or find_pyproject(start))
