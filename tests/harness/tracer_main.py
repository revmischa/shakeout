"""Run a program under execution tracing and record every function body that starts.

Usage: python tracer_main.py OUT.json SCRIPT [ARGS...]

For each code object with CO_OPTIMIZED (functions, methods, lambdas, generators; *not* module or
class bodies) we record whether it ever ran "freely" (outside the module-level execution of its own
file) and, if not, the module-level line numbers that caused it to run. The soundness checker uses
this to tell definition-time execution of a removed statement (fine: removing the statement removes
that execution too) from a genuine call of removed code (a soundness bug).

This file is executed directly by a subprocess and must only use the standard library.
"""

import json
import os
import runpy
import sys

CO_OPTIMIZED = 0x1
MAX_LINES = 64

records = {}


def _module_line(code, frame):
    """Line of the nearest ``<module>`` frame of the same file above ``frame``, else None."""
    f = frame
    while f is not None:
        fc = f.f_code
        if fc.co_name == "<module>" and fc.co_filename == code.co_filename:
            return f.f_lineno
        f = f.f_back
    return None


def _record(code, frame) -> None:
    if not code.co_flags & CO_OPTIMIZED:
        return
    key = (os.path.realpath(code.co_filename), code.co_firstlineno, code.co_name)
    rec = records.get(key)
    if rec is None:
        rec = records[key] = {"free": False, "module_lines": []}
    if rec["free"]:
        return
    line = _module_line(code, frame)
    if line is None:
        rec["free"] = True
    elif line not in rec["module_lines"] and len(rec["module_lines"]) < MAX_LINES:
        rec["module_lines"].append(line)


def _install() -> None:
    if hasattr(sys, "monitoring"):
        mon = sys.monitoring
        tool = mon.PROFILER_ID

        def on_start(code, offset) -> None:
            _record(code, sys._getframe(1))

        mon.use_tool_id(tool, "shakeout-soundness")
        mon.register_callback(tool, mon.events.PY_START, on_start)
        mon.set_events(tool, mon.events.PY_START)
    else:  # Python < 3.12

        def profiler(frame, event, arg) -> None:
            if event == "call":
                _record(frame.f_code, frame)

        sys.setprofile(profiler)
        import threading

        threading.setprofile(profiler)


def _dump(out) -> None:
    data = [{"file": k[0], "firstlineno": k[1], "name": k[2], **v} for k, v in records.items()]
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def main() -> None:
    out, script, *args = sys.argv[1:]
    script = os.path.realpath(script)
    sys.argv = [script, *args]
    sys.path[0] = os.path.dirname(script)
    _install()
    try:
        runpy.run_path(script, run_name="__main__")
    finally:
        if hasattr(sys, "monitoring"):
            sys.monitoring.set_events(sys.monitoring.PROFILER_ID, 0)
        else:
            sys.setprofile(None)
        _dump(out)


main()
