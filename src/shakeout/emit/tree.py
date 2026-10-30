"""Tree target: a directory with one (shaken, line-preserving) file per module.

No runtime loader is involved, so tracebacks name real files with original line numbers.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..project import ShakeResult
from . import BuildResult, build_source, fix_coding_cookie, manifest, module_chunks, write_manifest


def write_tree(result: ShakeResult, out: Path) -> BuildResult:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    contents: dict[str, str] = {}
    by_file: dict[str, list[str]] = {}
    for name in sorted(result.loaded):
        by_file.setdefault(result.modules[name].filename, []).append(name)
    for name in sorted(result.loaded):
        mod = result.modules[name]
        sharing = by_file[mod.filename]
        if len(sharing) > 1:
            # One file serving two module names (the entry imported under its own name):
            # keep the union so both executions find what they need.
            kept = {st.index for n in sharing for st in result.modules[n].statements if st.kept}
            chunks = module_chunks(result, mod, kept, stub=False)
        else:
            chunks = module_chunks(result, mod)
        source = fix_coding_cookie(build_source(chunks))
        contents[name] = source
        if mod.filename.endswith("/"):  # namespace package
            (out / mod.filename).mkdir(parents=True, exist_ok=True)
            continue
        path = out / mod.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    meta = manifest(result, contents)
    write_manifest(out / "shakeout-manifest.json", meta)
    return BuildResult(result, out, out / result.modules["__main__"].filename, [], meta)
