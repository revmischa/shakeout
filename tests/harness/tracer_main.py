"""Run a program under execution tracing and record every function body that starts.

Usage: python tracer_main.py OUT.json SCRIPT [ARGS...]

For each code object with CO_OPTIMIZED (functions, methods, lambdas, generators; *not* module or
class bodies) we record whether it ever ran "freely" (outside the module-level execution of its own
file) and, if not, the module-level line numbers that caused it to run. The soundness checker uses
this to tell definition-time execution of a removed statement (fine: removing the statement removes
that execution too) from a genuine call of removed code (a soundness bug).

This file is executed directly by a subprocess and must only use the standard library.
"""

from __future__ import annotations

import inspect
import json
import runpy
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import CodeType, FrameType

CO_OPTIMIZED = 0x1
MAX_LINES = 64

records: dict[tuple[str, int, str], dict[str, object]] = {}


def _module_line(code: CodeType, frame: FrameType | None) -> int | None:
    """Line of the nearest ``<module>`` frame of the same file above ``frame``, else None."""
    while frame is not None:
        fc = frame.f_code
        if fc.co_name == "<module>" and fc.co_filename == code.co_filename:
            return frame.f_lineno
        frame = frame.f_back
    return None


def _record(code: CodeType, frame: FrameType | None) -> None:
    if not code.co_flags & CO_OPTIMIZED:
        return
    key = (str(Path(code.co_filename).resolve()), code.co_firstlineno, code.co_name)
    rec = records.setdefault(key, {"free": False, "module_lines": list[int]()})
    if rec["free"]:
        return
    lines = rec["module_lines"]
    assert isinstance(lines, list)
    lines = cast("list[int]", lines)
    line = _module_line(code, frame)
    if line is None:
        rec["free"] = True
    elif line not in lines and len(lines) < MAX_LINES:
        lines.append(line)


def _install() -> None:
    if sys.version_info >= (3, 12):
        mon = sys.monitoring
        tool = mon.PROFILER_ID

        def on_start(code: CodeType, _offset: int) -> None:
            here = inspect.currentframe()
            _record(code, here.f_back if here else None)

        mon.use_tool_id(tool, "shakeout-soundness")
        mon.register_callback(tool, mon.events.PY_START, on_start)
        mon.set_events(tool, mon.events.PY_START)
    else:

        def profiler(frame: FrameType, event: str, _arg: object) -> None:
            if event == "call":
                _record(frame.f_code, frame)

        sys.setprofile(profiler)
        threading.setprofile(profiler)


def _uninstall() -> None:
    if sys.version_info >= (3, 12):
        sys.monitoring.set_events(sys.monitoring.PROFILER_ID, 0)
    else:
        sys.setprofile(None)


def _dump(out: Path) -> None:
    data = [{"file": k[0], "firstlineno": k[1], "name": k[2], **v} for k, v in records.items()]
    out.write_text(json.dumps(data), encoding="utf-8")


def main() -> None:
    out, script, *args = sys.argv[1:]
    path = Path(script).resolve()
    sys.argv = [str(path), *args]
    sys.path[0] = str(path.parent)
    _install()
    try:
        runpy.run_path(str(path), run_name="__main__")
    finally:
        _uninstall()
        _dump(Path(out))


main()
