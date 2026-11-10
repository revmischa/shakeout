"""Discovery, per-module analysis and shaking: entry point -> ShakeResult."""

from __future__ import annotations

import ast
import builtins
import functools
import os
import re
import sys
import tokenize
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from shakeout.analysis.classes import ClassEffects
from shakeout.analysis.effects import (
    EffectRules,
    is_main_guard,
    is_type_checking_guard,
    qualify_target,
)
from shakeout.analysis.scopes import (
    ImportContext,
    attribute_chain,
    collect,
    module_bindings,
    module_prefixes,
)
from shakeout.graph import Reason, Resolver, Shaker, ShakeState
from shakeout.model import Diagnostic, DynamicUse, Module, ModuleTarget, Ref, Statement
from shakeout.options import Options
from shakeout.resolve import (
    Found,
    ModuleFinder,
    display_filename,
    environment_site_dirs,
    is_pure_external,
    is_stdlib,
    project_root,
    stdlib_path,
)
from shakeout.result import ShakeResult

if TYPE_CHECKING:
    from shakeout.analysis.scopes import Collected

__all__ = ["BuildError", "Project", "ShakeResult", "analyze"]

_BUILTINS = frozenset(dir(builtins))
_PRAGMA = re.compile(r"#\s*shakeout:\s*(pure|keep)\b")
_KEEP_PRAGMA = "'# shakeout: keep' pragma"
_WHOLE_KINDS = {
    "globals": "calls globals()",
    "vars": "calls vars()",
    "locals": "calls locals()",
    "eval": "calls eval()",
    "exec": "calls exec()",
    "sys.modules": "accesses sys.modules",
}
_UNPRAGMABLE = frozenset({"future", "docstring", "main_guard"})


class BuildError(Exception):
    """The input cannot be bundled (syntax error, missing entry, bad configuration)."""


class Project:
    """One analysis run: discover modules, classify statements, then shake."""

    def __init__(self, entry: Path, options: Options) -> None:
        """Prepare to analyze ``entry`` with ``options``."""
        self.entry = Path(os.path.realpath(entry))
        self.options = options
        entry_dir = self.entry.parent
        search = [
            str(entry_dir),
            *options.roots,
            *options.search_path,
            *environment_site_dirs(project_root(entry_dir)),
            *stdlib_path(),
        ]
        roots = [entry_dir, *(Path(r) for r in options.roots)]
        self.finder = ModuleFinder(search, roots, frozenset(options.include))
        self.modules: dict[str, Module] = {}
        self.externals: dict[str, str] = {}
        self.external_paths: dict[str, Path] = {}
        self.diagnostics: list[Diagnostic] = []
        self._missing: set[str] = set()
        self.entry_name = self.entry.stem if options.library else "__main__"

    # -- discovery -------------------------------------------------------------------------
    def discover(self) -> None:
        """Find every bundleable module reachable through imports from the entry point."""
        queue: deque[str] = deque()
        self._add_entry()
        queue.append(self.entry_name)
        for spec in self.options.keep:
            module, _, name = spec.partition(":")
            self._consider_all(module, queue)
            if name and name != "*":  # "pkg:plugin" may name a submodule nothing imports
                self._consider_all(f"{module}.{name}", queue)
        while queue:
            mod = self.modules[queue.popleft()]
            for st in mod.statements:
                for name in sorted(st.loads):
                    self._consider_all(name, queue)
                for use in st.dynamic:
                    if use.kind == "import-prefix":
                        self._expand_prefix(st, use, queue)

    def _add_entry(self) -> None:
        source = _read(self.entry)
        mod = Module(
            self.entry_name,
            self.entry,
            self.entry.name,
            is_package=False,
            source=source,
            is_entry=True,
        )
        self._register(mod, run_as_main=not self.options.library)

    def _register(self, mod: Module, *, run_as_main: bool = False) -> None:
        _ModuleAnalyzer(mod).run()
        mod.runs_as_main = run_as_main
        self.modules[mod.name] = mod

    def _consider_all(self, name: str, queue: deque[str]) -> None:
        for prefix in module_prefixes(name):
            self._consider(prefix, queue)

    def _consider(self, name: str, queue: deque[str]) -> None:
        if name in self.modules or name in self.externals or name in self._missing:
            return
        if name in sys.builtin_module_names:
            self.externals[name] = "stdlib" if is_pure_external(name) else "builtin"
            return
        found = self.finder.find(name)
        if found is None:
            if is_stdlib(name):  # e.g. collections.abc on 3.15 is installed by its parent
                self.externals[name] = "stdlib" if is_pure_external(name) else "builtin"
            else:
                self._missing.add(name)
            return
        if not self.finder.bundleable(found):
            self.externals[name] = "stdlib" if is_pure_external(name) else found.kind
            location = found.origin or (
                Path(found.search_locations[0]) if found.search_locations else None
            )
            if location is not None and not is_stdlib(name):
                self.external_paths[name] = location
            return
        self._register(_module_from(name, found))
        queue.append(name)

    def _expand_prefix(self, st: Statement, use: DynamicUse, queue: deque[str]) -> None:
        package = use.detail.rstrip(".")
        self._consider_all(package, queue)
        found = self.finder.find(package)
        if package not in self.modules or found is None:
            return
        for sub in self.finder.submodules(found):
            self._consider_all(sub, queue)
            if sub in self.modules:
                st.loads.add(sub)
                st.refs.append(Ref(ModuleTarget(sub), (), use.line))

    # -- effects ---------------------------------------------------------------------------
    def qualify(self, mod: Module, expr: ast.expr) -> str | None:
        """Dotted name of what ``expr`` refers to, e.g. ``typing.TypeVar`` or ``lib.make``."""
        if isinstance(expr, ast.Subscript):
            expr = expr.value
        chain = attribute_chain(expr)
        if chain is None:
            return None
        root, attrs = chain
        idxs = mod.binders.get(root)
        if not idxs:
            if mod.has_star_import or root not in _BUILTINS:
                return None
            return ".".join(("builtins", root, *attrs))
        targets = {t for i in idxs for t in mod.statements[i].binds[root]}
        if len(targets) != 1:
            return None
        (target,) = targets
        if target is None:
            return ".".join((mod.name, root, *attrs))
        return qualify_target(target, attrs)

    def classify(self) -> None:
        """Decide for every statement whether it has side effects and must always run."""
        extra = frozenset(self.options.pure_calls)
        for mod in self.modules.values():
            rules = EffectRules(functools.partial(self.qualify, mod), extra)
            for st in mod.statements:
                _StatementClassifier(mod, st, rules).run()
        ClassEffects(
            self.modules,
            Resolver(self.modules),
            self.qualify,
            pure_calls=extra,
            assume_pure_bases=self.options.assume_pure_bases,
        ).classify_all()
        for st in self._all_statements():
            if st.pragma_pure and st.kind not in _UNPRAGMABLE:
                st.impure = None
            if st.keep_pragma:
                st.impure = st.impure or _KEEP_PRAGMA
        _ImportEffects(self).propagate()
        for st in self._all_statements():
            if st.impure and not st.root:
                st.root = st.impure

    def _all_statements(self) -> list[Statement]:
        return [st for mod in self.modules.values() for st in mod.statements]

    def external_impurity(self, name: str, *, required: bool) -> str | None:
        """Why importing the non-bundled module ``name`` is a side effect, if it is."""
        kind = self.externals.get(name)
        if kind is not None:
            return None if kind == "stdlib" else f"imports external module {name!r}"
        if name in self._missing and required:
            return f"imports {name!r}, which cannot be found"
        return None

    # -- shaking ---------------------------------------------------------------------------
    def shake(self) -> ShakeResult:
        """Compute what is kept and package it as a ShakeResult."""
        shaker = Shaker(self.modules, Resolver(self.modules))
        state = self._keep_everything(shaker) if not self.options.shake else self._reach(shaker)
        for st in self._all_statements():
            st.kept = (st.module, st.index) in state.live
        _Diagnoser(self, state).run()
        return ShakeResult(
            self.entry,
            self.entry_name,
            self.options,
            self.modules,
            state.loaded,
            state.live,
            state.reasons,
            self.diagnostics,
            dict(self.externals),
            dict(self.external_paths),
            set(state.whole),
            {k: set(v) for k, v in state.used.items()},
        )

    def _keep_everything(self, shaker: Shaker) -> ShakeState:
        state = shaker.state
        for st in self._all_statements():
            state.loaded.add(st.module)
            state.live.add((st.module, st.index))
            state.reasons[st.module, st.index] = Reason("noshake", "shaking disabled")
        state.loaded.update(self.modules)
        return state

    def _reach(self, shaker: Shaker) -> ShakeState:
        shaker.load(self.entry_name)
        for export in self.options.exports:
            module, _, name = export.rpartition(":")
            if not shaker.keep_symbol(module or self.entry_name, name, why=f"export {export}"):
                self._missing_definition("export", export, name)
        for spec in self.options.keep:
            module, _, name = spec.partition(":")
            if not name or name == "*":
                shaker.make_whole(module, f"keep option {spec}")
            elif module in self.modules and not shaker.keep_symbol(module, name):
                self._missing_definition("keep option", spec, name)
        return shaker.run()

    def _missing_definition(self, what: str, spec: str, name: str) -> None:
        self.diagnostics.append(
            Diagnostic("warning", f"{what} {spec!r}: nothing in the bundle defines {name!r}")
        )


class _ModuleAnalyzer:
    """Splits a module into top-level statements and records bindings and references."""

    def __init__(self, mod: Module) -> None:
        self.mod = mod
        self.ctx = ImportContext(mod.name, is_package=mod.is_package)

    def run(self) -> None:
        mod = self.mod
        try:
            tree = ast.parse(mod.source, filename=mod.filename)
        except SyntaxError as exc:
            msg = f"{mod.filename}:{exc.lineno}: syntax error: {exc.msg}"
            raise BuildError(msg) from exc
        for index, (nodes, span) in enumerate(_group_by_lines(tree.body)):
            st = self._statement(index, nodes, span)
            mod.statements.append(st)
            for name in st.binds:
                mod.binders.setdefault(name, []).append(index)
            mod.dynamic.extend(st.dynamic)
        for use in mod.dynamic:
            if use.kind in _WHOLE_KINDS and not mod.whole_reason:
                mod.whole_reason = f"{mod.filename}:{use.line} {_WHOLE_KINDS[use.kind]}"

    def _statement(self, index: int, nodes: list[ast.stmt], span: tuple[int, int]) -> Statement:
        mod = self.mod
        st = Statement(
            mod.name, index, mod.origin or Path(mod.filename), *span, nodes, kind="other"
        )
        for node in nodes:
            for name, targets in module_bindings(node, self.ctx).items():
                bucket = st.binds.setdefault(name, [])
                bucket.extend(t for t in targets if t not in bucket)
            self._merge(st, collect(node, self.ctx))
        header = "".join(mod.lines[span[0] - 1 : max(span[0], nodes[0].lineno)])
        for match in _PRAGMA.finditer(header):
            if match.group(1) == "pure":
                st.pragma_pure = True
            else:
                st.root = _KEEP_PRAGMA
                st.keep_pragma = True
        return st

    def _merge(self, st: Statement, out: Collected) -> None:
        st.refs.extend(out.refs)
        st.loads |= out.loads
        st.eager_loads |= out.eager_loads
        st.required |= out.required
        st.dynamic.extend(out.dynamic)
        self.mod.file_uses.extend(out.file_uses)
        if out.star_sources:
            self.mod.has_star_import = True
        for name in out.globals_declared:
            st.binds.setdefault(name, [None])


class _StatementClassifier:
    """Sets ``kind``, ``impure``, ``root`` and ``dead`` on one statement."""

    def __init__(self, mod: Module, st: Statement, rules: EffectRules) -> None:
        self.mod = mod
        self.st = st
        self.rules = rules

    def run(self) -> None:
        st = self.st
        if len(st.nodes) > 1:
            reasons = [self._effect(n) for n in st.nodes]
            st.impure = next((r for r in reasons if r), None)
            has_import = any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in st.nodes)
            st.kind = "import" if has_import and not st.impure else "group"
            return
        st.impure = self._effect(st.nodes[0])

    def _effect(self, node: ast.stmt) -> str | None:
        st = self.st
        if any(_is_dunder(name) for name in st.binds) and not st.root:
            st.root = "binds a module dunder"
        special = self._special(node)
        if special is not None:
            st.kind = special
            return None
        return self._definition_effect(node)

    def _special(self, node: ast.stmt) -> str | None:
        """Kind of statements that are never impure; None for everything else."""
        st = self.st
        if st.index == 0 and _is_string_expr(node):
            st.root = "module docstring (__doc__)"
            return "docstring"
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            st.root = "__future__ import"
            return "future"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(alias.name == "*" for alias in node.names):
                st.root = "star import (binds names that cannot be listed statically)"
            return "import"
        if is_main_guard(node):
            if self.mod.runs_as_main:
                st.root = "entry __main__ block"
            else:
                st.dead = True
            return "main_guard"
        if is_type_checking_guard(node, self.rules.qualify):
            st.eager_loads = set()
            return "type_checking"
        return None

    def _definition_effect(self, node: ast.stmt) -> str | None:
        st = self.st
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            st.kind = "def"
            return self.rules.function_effect(node)
        if isinstance(node, ast.ClassDef):
            st.kind = "class"
            return self.rules.class_shell_effect(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            st.kind = "assign"
            return self.rules.assignment_effect(node)
        st.kind = "def" if type(node).__name__ == "TypeAlias" else "other"
        if st.kind == "def" or isinstance(node, ast.Pass) or _is_constant_expr(node):
            return None
        return f"{type(node).__name__.lower()} statement at module level"


class _ImportEffects:
    """Least fixpoint: a module has effects if it runs an impure statement or imports one."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.effectful: set[str] = set()

    def propagate(self) -> None:
        modules = self.project.modules
        changed = True
        while changed:
            changed = False
            for mod in modules.values():
                if mod.name not in self.effectful and self._module_has_effects(mod):
                    self.effectful.add(mod.name)
                    changed = True
        for mod in modules.values():
            mod.has_effects = mod.name in self.effectful
            for st in mod.statements:
                if st.kind == "import" and not st.impure:
                    st.impure = self.reason(st)

    def _module_has_effects(self, mod: Module) -> bool:
        return any(
            st.impure or (st.kind == "import" and self.reason(st))
            for st in mod.statements
            if not st.dead and st.kind != "type_checking"
        )

    def reason(self, st: Statement) -> str | None:
        """Why the imports in ``st`` have side effects (None if they have none)."""
        already = set(module_prefixes(st.module))  # loaded while the module executes
        for name in sorted(st.eager_loads - already):
            if name in self.project.modules:
                if name in self.effectful:
                    return f"imports {name!r}, which has side effects"
                continue
            reason = self.project.external_impurity(name, required=name in st.required)
            if reason:
                return reason
        return None


class _Diagnoser:
    """Warnings about things the analysis could not see through."""

    def __init__(self, project: Project, state: ShakeState) -> None:
        self.project = project
        self.state = state

    def run(self) -> None:
        for spec in self.project.options.keep:
            module = spec.partition(":")[0]
            if module not in self.project.modules:
                self._warn(f"keep option {spec!r}: module {module!r} was not found in the roots")
        for mod in sorted(self.project.modules.values(), key=lambda m: m.name):
            if mod.name in self.state.loaded:
                self._module(mod)

    def _warn(self, message: str, mod: Module | None = None, line: int = 0) -> None:
        name = mod.name if mod else ""
        self.project.diagnostics.append(Diagnostic("warning", message, name, line))

    def _live(self, st: Statement) -> bool:
        return (st.module, st.index) in self.state.live

    def _module(self, mod: Module) -> None:
        for st in mod.statements:
            if self._live(st):
                for use in st.dynamic:
                    self._dynamic(mod, use)
        for line in sorted(set(mod.file_uses)):
            if any(st.start <= line <= st.end and self._live(st) for st in mod.statements):
                self._warn(
                    f"{mod.filename}:{line} uses __file__; package data files ship with the tree "
                    "and lambda targets, but single and pyz bundles cannot read them",
                    mod,
                    line,
                )
        if mod.whole_reason:
            self.project.diagnostics.append(
                Diagnostic("info", f"kept {mod.name} whole: {mod.whole_reason}", mod.name)
            )

    def _dynamic(self, mod: Module, use: DynamicUse) -> None:
        where = f"{mod.filename}:{use.line}"
        if use.kind == "import":
            self._warn(
                f"dynamic import at {where}: the module name is computed at runtime; "
                "list possible modules in the keep option",
                mod,
                use.line,
            )
        elif use.kind == "import-prefix":
            package = use.detail.rstrip(".")
            self._warn(
                f"dynamic import at {where}: bundling every module under {package!r}", mod, use.line
            )


def _read(path: Path) -> str:
    with tokenize.open(path) as fh:
        text = fh.read()
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _module_from(name: str, found: Found) -> Module:
    source = _read(found.origin) if found.origin else ""
    return Module(name, found.origin, display_filename(name, found), found.is_package, source)


def _group_by_lines(body: list[ast.stmt]) -> list[tuple[list[ast.stmt], tuple[int, int]]]:
    """Top-level statements, merging those that share a source line."""
    groups: list[tuple[list[ast.stmt], tuple[int, int]]] = []
    for node in body:
        start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", ()))])
        end = node.end_lineno or node.lineno
        if groups and start <= groups[-1][1][1]:
            nodes, (first, last) = groups[-1]
            nodes.append(node)
            groups[-1] = (nodes, (first, max(end, last)))
        else:
            groups.append(([node], (start, end)))
    return groups


def _is_string_expr(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_constant_expr(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


_DUNDER_MIN_LENGTH = 5  # "__x__"


def _is_dunder(name: str) -> bool:
    return len(name) >= _DUNDER_MIN_LENGTH and name.startswith("__") and name.endswith("__")


def analyze(entry: Path, options: Options | None = None) -> ShakeResult:
    """Discover, classify and shake the program rooted at ``entry``."""
    project = Project(entry, options or Options())
    project.discover()
    project.classify()
    return project.shake()
