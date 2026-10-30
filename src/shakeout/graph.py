"""Cross-module name resolution and reachability ("shaking")."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .model import FromTarget, Module, ModuleTarget, NameTarget, Ref, Statement, Target

StmtKey = tuple[str, int]

# Attribute reads that expose a module's whole namespace.
_WHOLE_MODULE_ATTRS = frozenset({"__dict__"})


@dataclass
class Resolution:
    traversed: set[StmtKey] = field(default_factory=set)  # binders passed through (incl. imports)
    terminal: set[StmtKey] = field(default_factory=set)  # binders that define a value locally
    whole: set[str] = field(default_factory=set)  # modules escaping as values

    def merge(self, other: Resolution) -> None:
        self.traversed |= other.traversed
        self.terminal |= other.terminal
        self.whole |= other.whole


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
    live: set[StmtKey] = field(default_factory=set)
    loaded: set[str] = field(default_factory=set)
    whole: set[str] = field(default_factory=set)
    reasons: dict[StmtKey, Reason] = field(default_factory=dict)


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
            self.mark((module, st.index), Reason("whole", why))

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
        for mod in sorted(res.whole):
            self.make_whole(mod, f"keep option {module}:{name}")

    def run(self) -> ShakeState:
        while self._queue:
            key = self._queue.popleft()
            st = self.stmt(key)
            self.load(st.module)
            for name in sorted(st.loads):
                self.load(name)
            self._require_imported_names(key, st)
            for ref in st.refs:
                res = self.resolver.resolve_ref(st.module, ref)
                label = ".".join((_base_name(ref), *ref.attrs))
                for target in sorted(res.traversed):
                    self.mark(target, Reason("ref", label, key))
                for mod in sorted(res.whole):
                    self.make_whole(mod, f"module object used as a value ({label}) at {st.id}")
        return self.state


    def _require_imported_names(self, key: StmtKey, st: Statement) -> None:
        """A kept ``from x import y`` fails at runtime unless ``y`` exists, so keep its binders.

        (A submodule only has to be loaded, which ``loads`` already guarantees.)
        """
        for name in sorted(st.binds):
            for target in st.binds[name]:
                if isinstance(target, FromTarget):
                    res = self.resolver.resolve(st.module, target, ())
                    for binder in sorted(res.traversed):
                        self.mark(binder, Reason("ref", f"imported by {st.id}", key))


def _base_name(ref: Ref) -> str:
    base = ref.base
    if isinstance(base, NameTarget):
        return base.name
    if isinstance(base, ModuleTarget):
        return base.module
    return f"{base.module}.{base.attr}"
