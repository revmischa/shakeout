"""Human-readable analysis report (also the golden-file format)."""

from __future__ import annotations

from .project import ShakeResult


def _first_line(text: str, width: int = 48) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= width else line[: width - 3] + "..."


def render_report(result: ShakeResult) -> str:
    out: list[str] = []
    for name in sorted(result.modules, key=lambda n: (n != "__main__", n)):
        mod = result.modules[name]
        status = "bundled" if name in result.loaded else "dropped"
        extra = f", kept whole: {mod.whole_reason}" if mod.whole_reason and status == "bundled" else ""
        out.append(f"module {name} [{mod.filename}] {status}{extra}")
        lines = mod.lines
        for st in mod.statements:
            mark = "+" if st.kept else "-"
            text = _first_line("".join(lines[st.start - 1 : st.end]))
            span = f"L{st.start}-{st.end}" if st.end != st.start else f"L{st.start}"
            why = ""
            if st.kept:
                reason = result.reasons[(name, st.index)]
                why = reason.detail
                if reason.kind == "ref" and not why.startswith("imported by"):
                    why = f"used as {why}"
                why = f"  [{reason.kind}: {why}]" if reason.kind != "ref" else f"  [{why}]"
            elif st.dead:
                why = "  [dead: not the entry module]"
            out.append(f"  {mark} {span:<9} {text}{why}".rstrip())
    externals = sorted(n for n in result.externals if "." not in n)
    if externals:
        out.append("external: " + ", ".join(externals))
    for diag in result.diagnostics:
        out.append(str(diag))
    return "\n".join(out) + "\n"
