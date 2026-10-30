"""Single-file target: one runnable (or importable) .py with an in-memory importer."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from shakeout import __version__, runtime
from shakeout.emit import BuildResult, ChunkBuilder, Chunks, build_source, manifest, write_manifest

if TYPE_CHECKING:
    from pathlib import Path

    from shakeout.result import ShakeResult


def write_single(result: ShakeResult, out: Path) -> BuildResult:
    """Write ``out/<entry stem>.py``.

    Run it as a script, or (with ``exports``) import it like the original entry module.
    """
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.entry.stem}.py"
    builder = ChunkBuilder(result)
    modules: dict[str, tuple[str, bool, Chunks]] = {}
    contents: dict[str, str] = {}
    for name in sorted(result.loaded):
        mod = result.modules[name]
        if mod.is_entry:
            continue
        chunks = builder.chunks(mod)
        filename = mod.filename + "__init__.py" if mod.filename.endswith("/") else mod.filename
        modules[name] = (filename, mod.is_package, chunks)
        contents[name] = build_source(chunks)
    entry = result.entry_mod
    entry_chunks = builder.chunks(entry)
    contents[entry.name] = build_source(entry_chunks)
    meta = manifest(result, contents, "single")
    # Via inspect (not a file read) so a bundled shakeout can still produce bundles.
    runtime_source = inspect.getsource(runtime)
    lines = [
        "#!/usr/bin/env python3",
        f"# Bundled by shakeout {__version__} from {result.entry.name}. Do not edit.",
        f"# build {meta['build_id']}; source line numbers are preserved.",
        runtime_source.rstrip("\n"),
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
    write_manifest(out / f"{result.entry.stem}.manifest.json", meta)
    return BuildResult(result, out, path, [str(path)], meta)
