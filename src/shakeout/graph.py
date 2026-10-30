"""Cross-module name resolution and reachability ("shaking")."""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field

from .model import FromTarget, Module, ModuleTarget, NameTarget, Ref, Statement, Target

StmtKey = tuple[str, int]

# Attribute reads that expose a module's whole namespace.
_WHOLE_MODULE_ATTRS = frozenset({"__dict__"})


@dataclass
class Resolution:
    traversed: set[StmtKey] = field(default_factory=set[StmtKey])  # binders passed through (incl. imports)
    terminal: set[StmtKey] = field(default_factory=set[StmtKey])  # binders that define a value locally
    whole: set[str] = field(default_factory=set[str])  # modules escaping as values
    names: set[tuple[str, int, str]] = field(default_factory=set[tuple[str, int, str]])  # (module, stmt, bound name)

    def merge(self, other: Resolution) -> None:
        self.traversed |= other.traversed
        self.terminal |= other.terminal
        self.whole |= other.whole
        self.names |= other.names


class Resolver:
    """Follows ``name.attr.attr`` through imports and re-exports to the defining statements."""

    def __init__(self, modules: dict[str, Module]) -> None:
        self.modules = modules
        self._memo: dict[tuple[str, str, tuple[str, ...]], Resolution] = {}
        self._active: set[tuple[str, str, tuple[str, ...]]] = set()

    def resolve(self, module: str, base: Target, attrs: tuple[str, ...]) -> Resolution:
        if isinstance(base, NameTarget):
            return self._name(module, base.name, attrs)
        if isinstance(base, ModuleTarget):
            return self._in_module(base.module, attrs)
        assert isinstance(base, FromTarget)
        return self._name(base.module, base.attr, attrs)

    def resolve_ref(self, module: str, ref: Ref) -> Resolution:
        return self.resolve(module, ref.base, ref.attrs)

    def _in_module(self, module: str, attrs: tuple[str, ...]) -> Resolution:
        if module not in self.modules:
            return Resolution()
        if not attrs or attrs[0] in _WHOLE_MODULE_ATTRS:
            return Resolution(whole={module})
        return self._name(module, attrs[0], attrs[1:])

    def _name(self, module: str, name: str, attrs: tuple[str, ...]) -> Resolution:
        key = (module, name, attrs)
        if key in self._memo:
            return self._memo[key]
        mod = self.modules.get(module)
        if mod is None or key in self._active:
            return Resolution()
        self._active.add(key)
        res = Resolution()
        for idx in mod.binders.get(name, ()):
            res.traversed.add((module, idx))
            res.names.add((module, idx, name))
            for target in mod.statements[idx].binds[name]:
                if target is None:
                    res.terminal.add((module, idx))
                else:
                    res.merge(self.resolve(module, target, attrs))
        submodule = f"{module}.{name}"
        if submodule in self.modules:
            res.merge(self._in_module(submodule, attrs))
        self._active.discard(key)
        self._memo[key] = res
        return res


@dataclass(frozen=True)
class Reason:
    kind: str  # "root" | "ref" | "whole" | "keep" | "noshake"
    detail: str
    parent: StmtKey | None = None


@dataclass
class ShakeState:
    live: set[StmtKey] = field(default_factory=set[StmtKey])
    loaded: set[str] = field(default_factory=set[str])
    whole: set[str] = field(default_factory=set[str])
    reasons: dict[StmtKey, Reason] = field(default_factory=dict[StmtKey, Reason])
    used: dict[StmtKey, set[str]] = field(default_factory=dict[StmtKey, set[str]])  # bound names actually used


class Shaker:
    def __init__(self, modules: dict[str, Module], resolver: Resolver) -> None:
        self.modules = modules
        self.resolver = resolver
        self.state = ShakeState()
        self._queue: deque[StmtKey] = deque()

    def stmt(self, key: StmtKey) -> Statement:
        return self.modules[key[0]].statements[key[1]]

    def load(self, module: str) -> None:
        mod = self.modules.get(module)
        if mod is None or module in self.state.loaded:
            return
        self.state.loaded.add(module)
        parent = module.rpartition(".")[0]
        if parent:
            self.load(parent)
        for st in mod.statements:
            if st.root and not st.dead:
                self.mark((module, st.index), Reason("root", st.root))
        if mod.whole_reason:
            self.make_whole(module, mod.whole_reason)

    def make_whole(self, module: str, why: str) -> None:
        if module not in self.modules:
            return
        self.load(module)
        if module in self.state.whole:
            return
        self.state.whole.add(module)
        for st in self.modules[module].statements:
            key = (module, st.index)
            self.mark(key, Reason("whole", why))
            for name in sorted(st.binds):  # whole modules are emitted verbatim
                self._use_name(key, name)

    def mark(self, key: StmtKey, reason: Reason) -> None:
        if key in self.state.live or self.stmt(key).dead:
            return
        self.state.live.add(key)
        self.state.reasons[key] = reason
        self._queue.append(key)

    def keep_symbol(self, module: str, name: str) -> None:
        self.load(module)
        res = self.resolver.resolve(module, NameTarget(name), ())
        for key in sorted(res.traversed):
            self.mark(key, Reason("keep", f"keep option {module}:{name}"))
        self._use(res, None)
        for mod in sorted(res.whole):
            self.make_whole(mod, f"keep option {module}:{name}")

    def run(self) -> ShakeState:
        while self._queue:
            key = self._queue.popleft()
            st = self.stmt(key)
            self.load(st.module)
            for name in sorted(st.loads):
                self.load(name)
            if is_rewritable_import(st):
                for name in sorted(self.state.used.get(key, ())):
                    self._require_name(key, st, name)
            else:
                for name in sorted(st.binds):
                    self._require_name(key, st, name)
            for ref in st.refs:
                res = self.resolver.resolve_ref(st.module, ref)
                label = ".".join((_base_name(ref), *ref.attrs))
                for target in sorted(res.traversed):
                    self.mark(target, Reason("ref", label, key))
                self._use(res, key)
                for mod in sorted(res.whole):
                    self.make_whole(mod, f"module object used as a value ({label}) at {st.id}")
        return self.state


    def _use(self, res: Resolution, parent: StmtKey | None) -> None:
        """Record which bound names were used; a used ``from x import y`` needs ``y`` to exist."""
        for module, idx, name in sorted(res.names):
            self._use_name((module, idx), name)

    def _use_name(self, key: StmtKey, name: str) -> None:
        used = self.state.used.setdefault(key, set())
        if name in used:
            return
        used.add(name)
        st = self.stmt(key)
        if key in self.state.live and is_rewritable_import(st):
            self._require_name(key, st, name)

    def _require_name(self, key: StmtKey, st: Statement, name: str) -> None:
        """A kept ``from x import y`` fails at runtime unless ``y`` exists, so keep its binders.

        Rewritable imports only require the names that are used (the rest are dropped from the
        statement when it is emitted); other statements require every name they import. A
        submodule only has to be loaded, which ``loads`` already guarantees.
        """
        for target in st.binds.get(name, ()):
            if isinstance(target, FromTarget):
                res = self.resolver.resolve(st.module, target, ())
                for binder in sorted(res.traversed):
                    self.mark(binder, Reason("ref", f"imported by {st.id}", key))
                self._use(res, key)


def is_rewritable_import(st: Statement) -> bool:
    """A single top-level import statement whose unused names can be dropped when emitted."""
    if st.kind != "import" or len(st.nodes) != 1:
        return False
    node = st.nodes[0]
    if isinstance(node, ast.ImportFrom):
        return node.module != "__future__" and all(a.name != "*" for a in node.names)
    return isinstance(node, ast.Import)


def _base_name(ref: Ref) -> str:
    base = ref.base
    if isinstance(base, NameTarget):
        return base.name
    if isinstance(base, ModuleTarget):
        return base.module
    return f"{base.module}.{base.attr}"
