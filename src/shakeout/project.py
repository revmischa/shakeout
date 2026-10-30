"""Discovery, per-module analysis and shaking: entry point -> ShakeResult."""

from __future__ import annotations

import ast
import builtins
import os
import re
import sys
import tokenize
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .analysis.effects import (
    PURE_BASE_MODULES,
    PURE_BASES,
    EffectRules,
    is_main_guard,
    is_type_checking_guard,
    qualify_target,
)
from .analysis.scopes import ImportContext, attribute_chain, collect, module_bindings
from .graph import Reason, Resolver, Shaker, StmtKey, is_rewritable_import
from .model import Diagnostic, DynamicUse, Module, ModuleTarget, NameTarget, Ref, Statement
from .options import Options
from .resolve import Found, ModuleFinder, display_filename, is_pure_external, is_stdlib

_BUILTINS = frozenset(dir(builtins))
_PRAGMA = re.compile(r"#\s*shakeout:\s*(pure|keep)\b")
_WHOLE_KINDS = {
    "globals": "calls globals()",
    "vars": "calls vars()",
    "locals": "calls locals()",
    "eval": "calls eval()",
    "exec": "calls exec()",
    "sys.modules": "accesses sys.modules",
}


class BuildError(Exception):
    pass


@dataclass
class ShakeResult:
    entry: Path
    options: Options
    modules: dict[str, Module]
    loaded: set[str]
    live: set[StmtKey]
    reasons: dict[StmtKey, Reason]
    diagnostics: list[Diagnostic]
    externals: dict[str, str] = field(default_factory=dict[str, str])  # name -> "stdlib"/"third-party"/...
    whole: set[str] = field(default_factory=set[str])
    used: dict[StmtKey, set[str]] = field(default_factory=dict[StmtKey, set[str]])
    _rewrites: dict[StmtKey, tuple[str, frozenset[str]] | None] = field(
        default_factory=dict[StmtKey, tuple[str, frozenset[str]] | None])

    # -- import rewriting ------------------------------------------------------------------
    def rewrite(self, st: Statement) -> tuple[str, frozenset[str]] | None:
        """(replacement source, dropped names) for a kept import with unused names, else None.

        ``from m import a, b`` with only ``a`` used becomes ``from m import a``. If no name is
        used the statement only runs for its side effects and becomes ``import m as _shakeout_N``.
        Imports of external modules are never rewritten (a missing name must still fail), and a
        dropped submodule with side effects is still imported.
        """
        key = (st.module, st.index)
        if key not in self._rewrites:
            self._rewrites[key] = self._compute_rewrite(st)
        return self._rewrites[key]

    def _compute_rewrite(self, st: Statement) -> tuple[str, frozenset[str]] | None:
        key = (st.module, st.index)
        if (not self.options.shake or key not in self.live or st.module in self.whole
                or not is_rewritable_import(st)):
            return None
        node = st.nodes[0]
        used = self.used.get(key, set())
        mod = self.modules[st.module]
        alias_name = f"_shakeout_import_{st.start}"
        keep: list[ast.alias] = []
        dropped: set[str] = set()
        if isinstance(node, ast.ImportFrom):
            base = ImportContext(mod.name, mod.is_package).absolute(node.level, node.module)
            if base is None or base not in self.modules:
                return None
            for a in node.names:
                bound = a.asname or a.name
                sub = self.modules.get(f"{base}.{a.name}")
                if bound in used or (sub is not None and sub.has_effects):
                    keep.append(a)
                else:
                    dropped.add(bound)
            if not dropped:
                return None
            if keep:
                new: ast.stmt = ast.ImportFrom(module=node.module, names=keep, level=node.level)
            else:
                new = ast.Import(names=[ast.alias(name=base, asname=alias_name)])
        else:
            # ``import x`` never needs attributes to exist, so only names of pure (droppable)
            # bundled modules are removed; everything else stays verbatim, which also keeps
            # traceback text exact for errors raised while importing.
            assert isinstance(node, ast.Import)
            for a in node.names:
                bound = a.asname or a.name.split(".")[0]
                effectful = any(self.modules[p].has_effects for p in _prefixes(a.name)
                                if p in self.modules)
                if bound in used or a.name not in self.modules or effectful:
                    keep.append(a)
                else:
                    dropped.add(bound)
            if not dropped:
                return None
            if not keep:
                return ("pass  # shakeout: removed " + ast.unparse(node), frozenset(dropped))
            new = ast.Import(names=keep)
        note = f"  # shakeout: was {ast.unparse(node)!r}"
        return (ast.unparse(new) + note, frozenset(dropped))

    # -- public, symbol-level view ---------------------------------------------------------
    @property
    def statements(self) -> list[Statement]:
        return [st for mod in self.modules.values() for st in mod.statements]

    @property
    def bundled_modules(self) -> frozenset[str]:
        return frozenset(self.loaded)

    @property
    def dropped_modules(self) -> frozenset[str]:
        return frozenset(set(self.modules) - self.loaded)

    def _symbols(self) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for mod in self.modules.values():
            for name, idxs in mod.binders.items():
                out[f"{mod.name}:{name}"] = any(self.binds_at_runtime(mod.statements[i], name)
                                               for i in idxs)
        return out

    def binds_at_runtime(self, st: Statement, name: str) -> bool:
        """Whether the emitted bundle still binds ``name`` through statement ``st``."""
        if (st.module, st.index) not in self.live:
            return False
        rw = self.rewrite(st)
        return rw is None or name not in rw[1]

    @property
    def kept(self) -> frozenset[str]:
        return frozenset(s for s, live in self._symbols().items() if live)

    @property
    def removed(self) -> frozenset[str]:
        return frozenset(s for s, live in self._symbols().items() if not live)

    def why(self, symbol: str) -> list[str]:
        """Chain of reasons that kept ``module:name``, innermost first."""
        module, _, name = symbol.partition(":")
        mod = self.modules.get(module)
        if mod is None:
            return [f"{module} is not a bundled module"]
        live = [i for i in mod.binders.get(name, ()) if (module, i) in self.live]
        if not live:
            return [f"{symbol} was removed"]
        chain: list[str] = []
        key: StmtKey | None = (module, live[0])
        seen: set[StmtKey] = set()
        while key is not None and key not in seen:
            seen.add(key)
            reason = self.reasons[key]
            st = self.modules[key[0]].statements[key[1]]
            chain.append(f"{st.id} ({reason.kind}: {reason.detail})")
            key = reason.parent
        return chain


class Project:
    def __init__(self, entry: Path, options: Options) -> None:
        self.entry = Path(os.path.realpath(entry))
        self.options = options
        entry_dir = self.entry.parent
        roots = [entry_dir, *(Path(r) for r in options.roots)]
        search = [str(entry_dir), *(str(r) for r in options.roots),
                  *(p for p in sys.path[1:] if p and os.path.realpath(p) != os.getcwd())]
        self.finder = ModuleFinder(search, roots, frozenset(options.include))
        self.modules: dict[str, Module] = {}
        self.externals: dict[str, str] = {}
        self.diagnostics: list[Diagnostic] = []
        self._missing: set[str] = set()

    # -- discovery -------------------------------------------------------------------------
    def _read(self, path: Path) -> str:
        with tokenize.open(path) as fh:
            text = fh.read()
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _add(self, name: str, found: Found | None, *, entry: bool = False) -> Module:
        if entry:
            source = self._read(self.entry)
            mod = Module("__main__", self.entry, self.entry.name, False, source, is_entry=True)
        else:
            assert found is not None
            source = self._read(found.origin) if found.origin else ""
            mod = Module(name, found.origin, display_filename(name, found), found.is_package, source)
        self._analyze_module(mod)
        self.modules[mod.name] = mod
        return mod

    def _consider(self, name: str, queue: deque[str]) -> None:
        if name in self.modules or name in self.externals or name in self._missing:
            return
        if name in sys.builtin_module_names:
            self.externals[name] = "stdlib" if is_pure_external(name) else "builtin"
            return
        found = self.finder.find(name)
        if found is None:
            if is_stdlib(name):
                # e.g. collections.abc on 3.15 is installed by its parent, not a file.
                self.externals[name] = "stdlib" if is_pure_external(name) else "builtin"
            else:
                self._missing.add(name)
            return
        if not self.finder.bundleable(found):
            self.externals[name] = "stdlib" if is_pure_external(name) else found.kind
            return
        self._add(name, found)
        queue.append(name)

    def discover(self) -> None:
        queue: deque[str] = deque()
        self._add("__main__", None, entry=True)
        queue.append("__main__")
        for spec in self.options.keep:
            module = spec.partition(":")[0]
            for prefix in _prefixes(module):
                self._consider(prefix, queue)
        while queue:
            mod = self.modules[queue.popleft()]
            for st in mod.statements:
                for name in sorted(st.loads):
                    for prefix in _prefixes(name):
                        self._consider(prefix, queue)
                for use in st.dynamic:
                    if use.kind == "import-prefix":
                        self._expand_prefix(mod, st, use, queue)

    def _expand_prefix(self, mod: Module, st: Statement, use: DynamicUse, queue: deque[str]) -> None:
        package = use.detail.rstrip(".")
        for prefix in _prefixes(package):
            self._consider(prefix, queue)
        found = self.finder.find(package)
        if package not in self.modules or found is None:
            return
        subs = self.finder.submodules(found)
        for sub in subs:
            for prefix in _prefixes(sub):
                self._consider(prefix, queue)
            if sub in self.modules:
                st.loads.add(sub)
                st.refs.append(Ref(ModuleTarget(sub), (), use.line))

    # -- per-module analysis ---------------------------------------------------------------
    def _analyze_module(self, mod: Module) -> None:
        try:
            tree = ast.parse(mod.source, filename=mod.filename)
        except SyntaxError as exc:
            raise BuildError(f"{mod.filename}:{exc.lineno}: syntax error: {exc.msg}") from exc
        ctx = ImportContext(mod.name, mod.is_package)
        lines = mod.lines
        groups: list[list[ast.stmt]] = []
        spans: list[tuple[int, int]] = []
        for node in tree.body:
            start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", ()))])
            end = node.end_lineno or node.lineno
            if spans and start <= spans[-1][1]:
                groups[-1].append(node)
                spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
            else:
                groups.append([node])
                spans.append((start, end))
        for index, (nodes, (start, end)) in enumerate(zip(groups, spans, strict=False)):
            st = Statement(mod.name, index, mod.origin or Path(mod.filename), start, end, nodes,
                           kind="other")
            for node in nodes:
                for name, targets in module_bindings(node, ctx).items():
                    bucket = st.binds.setdefault(name, [])
                    bucket.extend(t for t in targets if t not in bucket)
                out = collect(node, ctx)
                st.refs.extend(out.refs)
                st.loads |= out.loads
                st.eager_loads |= out.eager_loads
                st.required |= out.required
                st.dynamic.extend(out.dynamic)
                mod.file_uses.extend(out.file_uses)
                if out.star_sources:
                    mod.has_star_import = True
                for name in out.globals_declared:
                    st.binds.setdefault(name, [None])
            header = "".join(lines[start - 1 : max(start, nodes[0].lineno)])
            for match in _PRAGMA.finditer(header):
                if match.group(1) == "pure":
                    st.pragma_pure = True
                else:
                    st.root = "'# shakeout: keep' pragma"
                    st.keep_pragma = True
            mod.statements.append(st)
            for name in st.binds:
                mod.binders.setdefault(name, []).append(index)
            mod.dynamic.extend(st.dynamic)
        for use in mod.dynamic:
            if use.kind in _WHOLE_KINDS and not mod.whole_reason:
                mod.whole_reason = f"{mod.filename}:{use.line} {_WHOLE_KINDS[use.kind]}"

    # -- names -----------------------------------------------------------------------------
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

    def _is_external_name(self, qualified: str) -> bool:
        parts = qualified.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in self.modules:
                return False
        return True

    # -- effects ---------------------------------------------------------------------------
    def classify(self) -> None:
        extra = frozenset(self.options.pure_calls)
        for mod in self.modules.values():
            rules = EffectRules(lambda e, m=mod: self.qualify(m, e), extra)
            for st in mod.statements:
                self._classify_statement(mod, st, rules)
        self._classify_classes()
        for mod in self.modules.values():
            for st in mod.statements:
                if st.pragma_pure and st.kind not in ("future", "docstring", "main_guard"):
                    st.impure = None
                if st.keep_pragma:
                    st.impure = st.impure or "'# shakeout: keep' pragma"
        self._propagate_import_effects()
        for mod in self.modules.values():
            for st in mod.statements:
                if st.impure and not st.root:
                    st.root = st.impure

    def _classify_statement(self, mod: Module, st: Statement, rules: EffectRules) -> None:
        if len(st.nodes) > 1:
            reasons = [self._node_effect(mod, st, n, rules) for n in st.nodes]
            st.kind = "group"
            st.impure = next((r for r in reasons if r), None)
            if any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in st.nodes):
                st.kind = "import" if not st.impure else "group"
            return
        node = st.nodes[0]
        st.impure = self._node_effect(mod, st, node, rules)

    def _node_effect(self, mod: Module, st: Statement, node: ast.stmt, rules: EffectRules) -> str | None:
        if st.index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            st.kind = "docstring"
            st.root = "module docstring (__doc__)"
            return None
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            st.kind = "future"
            st.root = "__future__ import"
            return None
        if any(_is_dunder(name) for name in st.binds) and not st.root:
            st.root = "binds a module dunder"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            st.kind = "import"
            if any(alias.name == "*" for alias in node.names):
                st.root = "star import (binds names that cannot be listed statically)"
            return None
        if is_main_guard(node):
            st.kind = "main_guard"
            if mod.is_entry:
                st.root = "entry __main__ block"
            else:
                st.dead = True
            return None
        if is_type_checking_guard(node, lambda e: self.qualify(mod, e)):
            st.kind = "type_checking"
            st.eager_loads = set()
            return None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            st.kind = "def"
            return rules.function_effect(node)
        if isinstance(node, ast.ClassDef):
            st.kind = "class"
            return rules.class_shell_effect(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            st.kind = "assign"
            return rules.assignment_effect(node)
        if type(node).__name__ == "TypeAlias" or isinstance(node, ast.Pass):
            st.kind = "def"
            return None
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            st.kind = "other"
            return None
        st.kind = "other"
        return f"{type(node).__name__.lower()} statement at module level"

    def _classify_classes(self) -> None:
        resolver = Resolver(self.modules)
        memo: dict[StmtKey, tuple[str | None, bool]] = {}

        def class_info(key: StmtKey, active: frozenset[StmtKey]) -> tuple[str | None, bool]:
            """(impurity reason from bases, is_registering) for a class statement."""
            if key in memo:
                return memo[key]
            if key in active:
                return ("cyclic class hierarchy", True)
            mod = self.modules[key[0]]
            node = mod.statements[key[1]].nodes[0]
            assert isinstance(node, ast.ClassDef)
            registering = any(k.arg == "metaclass" for k in node.keywords) or any(
                isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                and b.name == "__init_subclass__" for b in node.body)
            impure: str | None = None
            for base in node.bases:
                verdict = base_verdict(mod, base, active | {key})
                if verdict:
                    impure = impure or verdict
                    registering = True
            memo[key] = (impure, registering)
            return memo[key]

        def base_verdict(mod: Module, base: ast.expr, active: frozenset[StmtKey]) -> str | None:
            text = ast.unparse(base)
            expr = base.value if isinstance(base, ast.Subscript) else base
            chain = attribute_chain(expr)
            if chain is None:
                rules = EffectRules(lambda e: self.qualify(mod, e), frozenset(self.options.pure_calls))
                return None if rules.pure_expr(base) else f"base {text!r} may have side effects"
            q = self.qualify(mod, expr)
            if q is not None and self._is_external_name(q):
                if q.startswith("builtins.") or q in PURE_BASES \
                        or q.rpartition(".")[0] in PURE_BASE_MODULES:
                    return None
                if self.options.assume_pure_bases:
                    return None
                return f"base {text!r} comes from outside the bundle and may register subclasses"
            root, attrs = chain
            res = resolver.resolve(mod.name, NameTarget(root), attrs)
            if not res.terminal:
                return None if self.options.assume_pure_bases else f"base {text!r} cannot be resolved"
            for tkey in sorted(res.terminal):
                tst = self.modules[tkey[0]].statements[tkey[1]]
                if tst.kind == "class" and isinstance(tst.nodes[0], ast.ClassDef):
                    _, registering = class_info(tkey, active)
                    if registering:
                        return f"base {text!r} registers subclasses (__init_subclass__/metaclass)"
                elif not self.options.assume_pure_bases:
                    return f"base {text!r} is not a plain class definition"
            return None

        for mod in self.modules.values():
            for st in mod.statements:
                if st.kind == "class" and not st.impure:
                    reason, _ = class_info((mod.name, st.index), frozenset())
                    st.impure = reason

    def _external_impure(self, name: str, required: bool) -> str | None:
        kind = self.externals.get(name)
        if kind is not None:
            return None if kind == "stdlib" else f"imports external module {name!r}"
        if name in self._missing and required:
            return f"imports {name!r}, which cannot be found"
        return None

    def _propagate_import_effects(self) -> None:
        effectful: set[str] = set()

        def import_reason(st: Statement) -> str | None:
            # A module and its parent packages are already loaded while it executes.
            already = set(_prefixes(st.module))
            for name in sorted(st.eager_loads - already):
                if name in self.modules:
                    if name in effectful:
                        return f"imports {name!r}, which has side effects"
                else:
                    reason = self._external_impure(name, name in st.required)
                    if reason:
                        return reason
            return None

        changed = True
        while changed:
            changed = False
            for mod in self.modules.values():
                if mod.name in effectful:
                    continue
                for st in mod.statements:
                    if st.dead or st.kind == "type_checking":
                        continue
                    if st.impure or (st.kind == "import" and import_reason(st)):
                        effectful.add(mod.name)
                        changed = True
                        break
        for mod in self.modules.values():
            mod.has_effects = mod.name in effectful
            for st in mod.statements:
                if st.kind == "import" and not st.impure:
                    st.impure = import_reason(st)

    # -- shaking ---------------------------------------------------------------------------
    def shake(self) -> ShakeResult:
        resolver = Resolver(self.modules)
        shaker = Shaker(self.modules, resolver)
        if not self.options.shake:
            for mod in self.modules.values():
                shaker.state.loaded.add(mod.name)
                for st in mod.statements:
                    shaker.state.live.add((mod.name, st.index))
                    shaker.state.reasons[(mod.name, st.index)] = Reason("noshake", "shaking disabled")
            state = shaker.state
        else:
            shaker.load("__main__")
            for spec in self.options.keep:
                module, _, name = spec.partition(":")
                if not name or name == "*":
                    shaker.make_whole(module, f"keep option {spec}")
                else:
                    shaker.keep_symbol(module, name)
            state = shaker.run()
        for mod in self.modules.values():
            for st in mod.statements:
                st.kept = (mod.name, st.index) in state.live
        self._diagnose(state.live, state.loaded)
        return ShakeResult(self.entry, self.options, self.modules, state.loaded, state.live,
                           state.reasons, self.diagnostics, dict(self.externals),
                           set(state.whole), {k: set(v) for k, v in state.used.items()})

    def _diagnose(self, live: set[StmtKey], loaded: set[str]) -> None:
        for spec in self.options.keep:
            module = spec.partition(":")[0]
            if module not in self.modules:
                self.diagnostics.append(Diagnostic(
                    "warning", f"keep option {spec!r}: module {module!r} was not found in the bundle roots"))
        for mod in sorted(self.modules.values(), key=lambda m: m.name):
            if mod.name not in loaded:
                continue
            for st in mod.statements:
                if (mod.name, st.index) not in live:
                    continue
                for use in st.dynamic:
                    where = f"{mod.filename}:{use.line}"
                    if use.kind == "import":
                        self.diagnostics.append(Diagnostic(
                            "warning",
                            f"dynamic import at {where}: the module name is computed at runtime; "
                            "list possible modules in the keep option", mod.name, use.line))
                    elif use.kind == "import-prefix":
                        self.diagnostics.append(Diagnostic(
                            "warning",
                            f"dynamic import at {where}: bundling every module under "
                            f"{use.detail.rstrip('.')!r}", mod.name, use.line))
            for line in sorted(set(mod.file_uses)):
                if any(st.start <= line <= st.end and (mod.name, st.index) in live
                       for st in mod.statements):
                    self.diagnostics.append(Diagnostic(
                        "warning",
                        f"{mod.filename}:{line} uses __file__; files next to the module are not "
                        "bundled and __file__ points to a virtual path", mod.name, line))
            if mod.whole_reason:
                self.diagnostics.append(Diagnostic(
                    "info", f"kept {mod.name} whole: {mod.whole_reason}", mod.name))


def _prefixes(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def analyze(entry: Path, options: Options | None = None) -> ShakeResult:
    project = Project(entry, options or Options())
    project.discover()
    project.classify()
    return project.shake()
