"""Single-file target: one runnable .py with an in-memory importer."""

from __future__ import annotations

from pathlib import Path

from .. import __version__
from ..project import ShakeResult
from . import BuildResult, build_source, manifest, module_chunks

_RUNTIME = Path(__file__).resolve().parent.parent / "runtime.py"


def _literal(value: object) -> str:
    return repr(value)


def write_single(result: ShakeResult, out: Path) -> BuildResult:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.entry.stem}.py"
    modules: dict[str, tuple[str, bool, list[tuple[int, str]]]] = {}
    contents: dict[str, str] = {}
    for name in sorted(result.loaded):
        mod = result.modules[name]
        if mod.is_entry:
            continue
        chunks = module_chunks(result, mod)
        filename = mod.filename if not mod.filename.endswith("/") else mod.filename + "__init__.py"
        modules[name] = (filename, mod.is_package, chunks)
        contents[name] = build_source(chunks)
    entry = result.modules["__main__"]
    entry_chunks = module_chunks(result, entry)
    contents["__main__"] = build_source(entry_chunks)
    meta = manifest(result, contents)
    runtime = _RUNTIME.read_text(encoding="utf-8")
    lines = [
        "#!/usr/bin/env python3",
        f"# Bundled by shakeout {__version__} from {result.entry.name}. Do not edit.",
        f"# build {meta['build_id']}; source line numbers are preserved.",
        runtime.rstrip("\n"),
        "",
        "_shakeout_modules = {",
    ]
    for name, (filename, is_pkg, chunks) in modules.items():
        lines.append(f"    {name!r}: ({filename!r}, {is_pkg!r}, (")
        lines.extend(f"        ({start}, {text!r})," for start, text in chunks)
        lines.append("    )),")
    lines.append("}")
    lines.append(f"_shakeout_entry = ({entry.filename!r}, (")
    lines.extend(f"    ({start}, {text!r})," for start, text in entry_chunks)
    lines.append("))")
    lines.append("_shakeout_meta = " + repr(meta))
    lines.append(
        "_shakeout_bootstrap(_shakeout_modules, _shakeout_entry, _shakeout_meta, globals())"
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return BuildResult(result, out, path, [str(path)], meta)
