"""Command-line interface: ``shakeout build|report|why``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .api import TARGETS, BuildError, Options, analyze, build, render_report
from .config import ConfigError, find_pyproject, load_config


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("entry", nargs="?", help="entry script (default: [tool.shakeout] entry)")
    common.add_argument("--keep", action="append", default=[], metavar="MOD[:NAME]",
                        help="always keep a module or symbol (repeatable)")
    common.add_argument("--include", action="append", default=[], metavar="PKG",
                        help="bundle and shake this third-party package (repeatable)")
    common.add_argument("--pure-call", action="append", default=[], metavar="QUALNAME",
                        help="treat calls to this function as side-effect free (repeatable)")
    common.add_argument("--assume-pure-bases", action="store_true", default=None,
                        help="assume unknown base classes do not register subclasses")
    common.add_argument("--root", action="append", default=[], metavar="DIR",
                        help="extra first-party source root (repeatable)")
    common.add_argument("--no-shake", action="store_true", help="bundle without removing code")
    common.add_argument("--config", type=Path, help="pyproject.toml to read (default: search)")

    parser = argparse.ArgumentParser(prog="shakeout", description="Tree-shaking bundler for Python.")
    parser.add_argument("--version", action="version", version=f"shakeout {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", parents=[common], help="write a bundle")
    b.add_argument("-o", "--out", type=Path, help="output directory (default: dist/)")
    b.add_argument("-t", "--target", choices=TARGETS, help="artifact type (default: single)")
    b.add_argument("--no-tombstones", action="store_true",
                   help="do not explain removed names when they are accessed at runtime")
    r = sub.add_parser("report", parents=[common], help="show what is kept and why")
    r.add_argument("--json", action="store_true", help="machine-readable output")
    w = sub.add_parser("why", parents=[common], help="explain why a symbol is kept")
    w.add_argument("symbol", help="module:name, e.g. pkg.core:helper")
    return parser


def _resolve(args: argparse.Namespace) -> tuple[Path, Options, dict[str, Any]]:
    start = Path(args.entry).resolve().parent if args.entry else Path.cwd()
    config = load_config(args.config or find_pyproject(start))
    entry = args.entry or config.get("entry")
    if not entry:
        raise ConfigError("no entry script given (pass one or set [tool.shakeout] entry)")
    options = Options(
        keep=(*config.get("keep", ()), *args.keep),
        include=(*config.get("include", ()), *args.include),
        pure_calls=(*config.get("pure-calls", ()), *args.pure_call),
        assume_pure_bases=bool(args.assume_pure_bases if args.assume_pure_bases is not None
                               else config.get("assume-pure-bases", False)),
        roots=(*config.get("roots", ()), *args.root),
        shake=not args.no_shake,
        tombstones=not getattr(args, "no_tombstones", False) and config.get("tombstones", True),
    )
    return Path(entry), options, config


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        entry, options, config = _resolve(args)
        if not entry.is_file():
            raise ConfigError(f"entry script {entry} does not exist")
        if args.command == "build":
            target = args.target or config.get("target", "single")
            out = args.out or Path(config.get("out", "dist"))
            built = build(entry, out, target=target, options=options)
            res = built.result
            for diag in res.diagnostics:
                if diag.level != "info":
                    print(diag, file=sys.stderr)
            kept = sum(st.kept for st in res.statements)
            total = len(res.statements)
            print(f"wrote {built.run_path} ({target}, build {built.manifest['build_id']}): "
                  f"{len(res.loaded)} modules, {kept}/{total} top-level statements kept, "
                  f"{len(res.dropped_modules)} modules dropped")
            return 0
        result = analyze(entry, options)
        if args.command == "report":
            if args.json:
                print(json.dumps({
                    "bundled": sorted(result.bundled_modules),
                    "dropped": sorted(result.dropped_modules),
                    "kept": sorted(result.kept),
                    "removed": sorted(result.removed),
                    "diagnostics": [vars(d) for d in result.diagnostics],
                }, indent=2))
            else:
                print(render_report(result), end="")
            return 0
        chain = result.why(args.symbol)
        for i, step in enumerate(chain):
            print(("  " * i) + ("<- " if i else "") + step)
        return 0 if args.symbol in result.kept else 1
    except (BuildError, ConfigError) as exc:
        print(f"shakeout: error: {exc}", file=sys.stderr)
        return 2
