# The shakeout runtime: embedded verbatim into single-file bundles.
#
# It must only use the standard library, define nothing at import time except
# ``_shakeout_bootstrap``, and stay compatible with every supported Python version.
# Each module is stored as chunks of (original start line, source text); padding with
# newlines before compile() restores the original line numbers, so tracebacks, warnings
# and inspect.getsource point at the real file and line without any source map.


def _shakeout_bootstrap(modules, entry, meta, main_globals):
    import linecache
    import sys
    from importlib.machinery import ModuleSpec

    def build_source(chunks):
        parts = []
        line = 1
        for start, text in chunks:
            if start > line:
                parts.append("\n" * (start - line))
                line = start
            parts.append(text)
            line += text.count("\n")
        return "".join(parts)

    def register_source(filename, source):
        if meta.get("embed_source", True):
            # Split on "\n" only (as for real files); str.splitlines also breaks on \f, \v, ...
            lines = [line + "\n" for line in source.split("\n")]
            if lines and lines[-1] == "\n":
                lines.pop()
            linecache.cache[filename] = (len(source), None, lines, filename)

    class ShakeoutImporter:
        """Meta path finder + loader serving bundled modules from memory."""

        def find_spec(self, fullname, path=None, target=None):
            info = modules.get(fullname)
            if info is None:
                return None
            filename, is_package, _ = info
            spec = ModuleSpec(fullname, self, origin=filename, is_package=is_package)
            spec.has_location = True
            return spec

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            filename, _, chunks = modules[module.__spec__.name]
            source = build_source(chunks)
            register_source(filename, source)
            exec(compile(source, filename, "exec", dont_inherit=True), module.__dict__)

        def get_source(self, fullname):
            info = modules.get(fullname)
            return None if info is None else build_source(info[2])

        def is_package(self, fullname):
            return modules[fullname][1]

    def add_note(exc, note):
        if hasattr(exc, "add_note"):
            exc.add_note(note)
        else:  # Python 3.10: no notes; the excepthook below prints __notes__ itself
            exc.__notes__ = [*getattr(exc, "__notes__", ()), note]

    def imported_name(exc):
        name_from = getattr(exc, "name_from", None)  # Python 3.12+
        if name_from is None and exc.args and isinstance(exc.args[0], str):
            prefix = "cannot import name '"
            message = exc.args[0]
            if message.startswith(prefix):
                name_from = message[len(prefix):].split("'", 1)[0]
        return name_from

    def annotate(exc):
        seen = set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            name = getattr(exc, "name", None)
            if isinstance(exc, ModuleNotFoundError) and name:
                if name in meta.get("dropped", ()):
                    add_note(
                        exc,
                        f"shakeout: module {name!r} was removed from this bundle because "
                        f"nothing imports it statically; keep it with: keep = [{name!r}]"
                    )
                elif meta.get("dynamic_imports"):
                    where = ", ".join(meta["dynamic_imports"])
                    add_note(
                        exc,
                        f"shakeout: {name!r} is not in this bundle. The build found dynamic "
                        f"imports it could not resolve at {where}. If {name!r} is one of "
                        f"them, add it to the keep option: keep = [{name!r}]"
                    )
            elif isinstance(exc, ImportError) and name and imported_name(exc):
                name_from = imported_name(exc)
                removed = meta.get("tombstones", {}).get(name, {}).get(name_from)
                if removed:
                    add_note(
                        exc,
                        f"shakeout: {name_from!r} was removed from {removed[0]}:{removed[1]} "
                        f"because nothing references it statically (it may be imported by "
                        f"generated code). Keep it with: keep = ['{name}:{name_from}']"
                    )
            elif isinstance(exc, NameError) and name:
                for module, tombstones in meta.get("tombstones", {}).items():
                    if name in tombstones:
                        filename, line = tombstones[name]
                        add_note(
                        exc,
                            f"shakeout: {name!r} was removed by shakeout from {filename}:{line} "
                            f"(unreferenced). Keep it with: keep = ['{module}:{name}']"
                        )
                        break
            exc = exc.__cause__ or exc.__context__

    if sys.version_info < (3, 13) and sys.excepthook is sys.__excepthook__:
        # Before 3.13 the default hook is written in C and reads source lines straight from
        # disk, bypassing linecache, so it cannot show bundled sources. traceback.py can.
        def excepthook(exc_type, exc, tb):
            import traceback

            traceback.print_exception(exc_type, exc, tb)
            if not hasattr(exc, "add_note"):  # 3.10's traceback does not print notes
                for note in getattr(exc, "__notes__", ()):
                    print(note, file=sys.stderr)

        sys.excepthook = excepthook

    sys.meta_path.insert(0, ShakeoutImporter())
    entry_filename, entry_chunks = entry
    source = build_source(entry_chunks)
    register_source(entry_filename, source)
    code = compile(source, entry_filename, "exec", dont_inherit=True)
    for key in [k for k in main_globals if k.startswith("_shakeout_")]:
        del main_globals[key]
    try:
        exec(code, main_globals)
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:
        annotate(exc)
        raise
