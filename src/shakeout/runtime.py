# The shakeout runtime, embedded verbatim at the top of every single-file bundle.
#
# Rules for this file (enforced by tests/unit/test_runtime.py):
# * standard library only; works on every supported Python version;
# * no module docstring (it would become the bundle's __doc__);
# * it must leave the program's __main__ namespace exactly as CPython would. Everything lives on
#   one class reached through ``self`` (function globals *are* __main__, so helpers must not be
#   looked up as globals once the module-level names are deleted before the entry runs), and
#   imports happen inside methods;
# * modules are stored as (original start line, text) chunks; padding with newlines before
#   compile() restores the original line numbers, so tracebacks, warnings and inspect point at
#   the real file and line without any source map.
from __future__ import annotations

TYPE_CHECKING = False  # the typing idiom without importing typing at startup
if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.machinery import ModuleSpec
    from types import CodeType, ModuleType, TracebackType
    from typing import TypedDict

    _ShakeoutChunks = Sequence[tuple[int, str]]
    _ShakeoutModules = dict[str, tuple[str, bool, _ShakeoutChunks]]

    class _ShakeoutMeta(TypedDict, total=False):
        dropped: list[str]
        dynamic_imports: list[str]
        tombstones: dict[str, dict[str, list[str | int]]]
        embed_source: bool


class _ShakeoutRuntime:
    """Meta path finder + loader for bundled modules, and the bundle's entry runner."""

    HOOK_BYPASSES_LINECACHE_BEFORE = (3, 13)

    def __init__(self, modules: _ShakeoutModules, meta: _ShakeoutMeta) -> None:
        # Imported once here: importing inside find_spec would re-enter this finder.
        from importlib.machinery import ModuleSpec  # noqa: PLC0415

        self.modules = modules
        self.meta = meta
        self.spec_class = ModuleSpec

    # -- sources ---------------------------------------------------------------------------
    @staticmethod
    def build_source(chunks: _ShakeoutChunks) -> str:
        parts: list[str] = []
        line = 1
        for start, text in chunks:
            if start > line:
                parts.append("\n" * (start - line))
                line = start
            parts.append(text)
            line += text.count("\n")
        return "".join(parts)

    def register_source(self, filename: str, source: str) -> None:
        import linecache  # noqa: PLC0415

        if not self.meta.get("embed_source", True):
            # An empty entry stops linecache from searching sys.path for a same-named file
            # (it would find and print lines of the bundle itself).
            linecache.cache[filename] = (0, None, [], filename)
            return
        # Split on "\n" only, as for real files; str.splitlines also breaks on \f, \v, ...
        lines = [line + "\n" for line in source.split("\n")]
        if lines and lines[-1] == "\n":
            lines.pop()
        linecache.cache[filename] = (len(source), None, lines, filename)

    def compile_chunks(self, filename: str, chunks: _ShakeoutChunks) -> CodeType:
        source = self.build_source(chunks)
        self.register_source(filename, source)
        return compile(source, filename, "exec", dont_inherit=True)

    # -- importlib protocol ----------------------------------------------------------------
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,  # noqa: ARG002 - MetaPathFinder protocol
        target: ModuleType | None = None,  # noqa: ARG002 - MetaPathFinder protocol
    ) -> ModuleSpec | None:
        info = self.modules.get(fullname)
        if info is None:
            return None
        filename, is_package, _ = info
        spec = self.spec_class(
            fullname,
            self,  # pyright: ignore[reportArgumentType] - implements the Loader protocol
            origin=filename,
            is_package=is_package,
        )
        spec.has_location = True
        return spec

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:  # noqa: ARG002 - protocol
        return None

    def exec_module(self, module: ModuleType) -> None:
        spec = module.__spec__
        if spec is None:  # pragma: no cover - importlib always sets it
            msg = f"module {module.__name__!r} has no spec"
            raise ImportError(msg)
        filename, _, chunks = self.modules[spec.name]
        exec(self.compile_chunks(filename, chunks), module.__dict__)  # noqa: S102 - the point

    def get_source(self, fullname: str) -> str | None:
        info = self.modules.get(fullname)
        if info is None or not self.meta.get("embed_source", True):
            return None
        return self.build_source(info[2])

    def is_package(self, fullname: str) -> bool:
        return self.modules[fullname][1]

    # -- diagnostics -----------------------------------------------------------------------
    @staticmethod
    def add_note(exc: BaseException, note: str) -> None:
        if hasattr(exc, "add_note"):
            exc.add_note(note)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        else:  # Python 3.10 has no notes; excepthook() prints __notes__ itself
            exc.__notes__ = [*getattr(exc, "__notes__", ()), note]  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    def imported_name(exc: ImportError) -> str | None:
        name_from: str | None = getattr(exc, "name_from", None)  # Python 3.12+
        prefix = "cannot import name '"
        if name_from is None and exc.args and isinstance(exc.args[0], str):
            message: str = exc.args[0]
            if message.startswith(prefix):
                name_from = message[len(prefix) :].split("'", 1)[0]
        return name_from

    def module_note(self, name: str) -> str | None:
        if name in self.meta.get("dropped", ()):
            return (
                f"shakeout: module {name!r} was removed from this bundle because nothing imports "
                f"it statically; keep it with: keep = [{name!r}]"
            )
        dynamic = self.meta.get("dynamic_imports", ())
        if dynamic:
            return (
                f"shakeout: {name!r} is not in this bundle. The build found dynamic imports it "
                f"could not resolve at {', '.join(dynamic)}. If {name!r} is one of them, add it "
                f"to the keep option: keep = [{name!r}]"
            )
        return None

    def removed_name_note(self, exc: BaseException, name: str) -> str | None:
        tombstones = self.meta.get("tombstones", {})
        if isinstance(exc, ImportError):
            name_from = self.imported_name(exc) or ""
            removed = tombstones.get(name, {}).get(name_from)
            if removed:
                return (
                    f"shakeout: {name_from!r} was removed from {removed[0]}:{removed[1]} because "
                    "nothing references it statically (it may be imported by generated code). "
                    f"Keep it with: keep = ['{name}:{name_from}']"
                )
            return None
        for module, stones in tombstones.items():
            if name in stones:
                filename, line = stones[name]
                return (
                    f"shakeout: {name!r} was removed by shakeout from {filename}:{line} "
                    f"(unreferenced). Keep it with: keep = ['{module}:{name}']"
                )
        return None

    def note_for(self, exc: BaseException) -> str | None:
        name: str | None = getattr(exc, "name", None)
        if not name:
            return None
        if isinstance(exc, ModuleNotFoundError):
            return self.module_note(name)
        if isinstance(exc, (ImportError, NameError)):
            return self.removed_name_note(exc, name)
        return None

    def annotate(self, exc: BaseException | None) -> None:
        seen: set[int] = set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            note = self.note_for(exc)
            if note:
                self.add_note(exc, note)
            exc = exc.__cause__ or exc.__context__

    @staticmethod
    def excepthook(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        import sys  # noqa: PLC0415
        import traceback  # noqa: PLC0415

        traceback.print_exception(exc_type, exc, tb)
        if not hasattr(exc, "add_note"):  # 3.10's traceback does not print notes
            for note in getattr(exc, "__notes__", ()):
                print(note, file=sys.stderr)  # noqa: T201

    # -- entry -----------------------------------------------------------------------------
    def run(self, entry: tuple[str, _ShakeoutChunks], main_globals: dict[str, object]) -> None:
        import sys  # noqa: PLC0415

        if sys.version_info < self.HOOK_BYPASSES_LINECACHE_BEFORE and (
            sys.excepthook is sys.__excepthook__
        ):
            # Before 3.13 the default hook is written in C and reads source lines straight from
            # disk, bypassing linecache, so it cannot show bundled sources. traceback.py can.
            sys.excepthook = self.excepthook
        sys.meta_path.insert(0, self)
        code = self.compile_chunks(*entry)
        for key in [k for k in main_globals if k.lower().startswith("_shakeout")]:
            del main_globals[key]
        for key, kind in (("annotations", "_Feature"), ("TYPE_CHECKING", "bool")):
            if type(main_globals.get(key)).__name__ == kind:
                del main_globals[key]  # bound by this file's own header
        try:
            exec(code, main_globals)  # noqa: S102 - running the entry script is the point
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException as exc:
            self.annotate(exc)
            raise


def _shakeout_bootstrap(  # pyright: ignore[reportUnusedFunction] - called by the bundle
    modules: _ShakeoutModules,
    entry: tuple[str, _ShakeoutChunks],
    meta: _ShakeoutMeta,
    main_globals: dict[str, object],
) -> None:
    _ShakeoutRuntime(modules, meta).run(entry, main_globals)
