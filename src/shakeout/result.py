"""The outcome of an analysis: what is kept, why, and how kept imports are rewritten."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shakeout.analysis.scopes import ImportContext, module_prefixes
from shakeout.graph import Reason, StmtKey, is_rewritable_import

if TYPE_CHECKING:
    from pathlib import Path

    from shakeout.model import Diagnostic, Module, Statement
    from shakeout.options import Options

Rewrite = tuple[str, frozenset[str]]  # (replacement source line, names no longer bound)


@dataclass
class ShakeResult:
    """Everything known after shaking one entry point."""

    entry: Path
    entry_module: str
    options: Options
    modules: dict[str, Module]
    loaded: set[str]
    live: set[StmtKey]
    reasons: dict[StmtKey, Reason]
    diagnostics: list[Diagnostic]
    externals: dict[str, str] = field(default_factory=dict[str, str])
    external_paths: dict[str, Path] = field(default_factory=dict[str, "Path"])  # pyright: ignore[reportUnknownArgumentType]
    whole: set[str] = field(default_factory=set[str])
    used: dict[StmtKey, set[str]] = field(default_factory=dict[StmtKey, set[str]])
    _rewrites: dict[StmtKey, Rewrite | None] = field(default_factory=dict[StmtKey, Rewrite | None])

    # -- symbol-level view -----------------------------------------------------------------
    @property
    def statements(self) -> list[Statement]:
        """All top-level statements of all discovered modules."""
        return [st for mod in self.modules.values() for st in mod.statements]

    @property
    def bundled_modules(self) -> frozenset[str]:
        """Modules shipped in the bundle."""
        return frozenset(self.loaded)

    @property
    def dropped_modules(self) -> frozenset[str]:
        """Modules discovered in the bundle roots but not shipped."""
        return frozenset(set(self.modules) - self.loaded)

    @property
    def entry_mod(self) -> Module:
        """The entry module."""
        return self.modules[self.entry_module]

    @property
    def live_statements(self) -> list[Statement]:
        """Statements that are kept."""
        return [st for st in self.statements if st.kept]

    @property
    def kept(self) -> frozenset[str]:
        """Symbols (``module:name``) still bound at runtime."""
        return frozenset(s for s, live in self._symbols().items() if live)

    @property
    def removed(self) -> frozenset[str]:
        """Symbols (``module:name``) that the bundle no longer binds."""
        return frozenset(s for s, live in self._symbols().items() if not live)

    def _symbols(self) -> dict[str, bool]:
        return {
            f"{mod.name}:{name}": any(self.binds_at_runtime(mod.statements[i], name) for i in idxs)
            for mod in self.modules.values()
            for name, idxs in mod.binders.items()
        }

    def binds_at_runtime(self, st: Statement, name: str) -> bool:
        """Whether the emitted bundle still binds ``name`` through statement ``st``."""
        if (st.module, st.index) not in self.live:
            return False
        rw = self.rewrite(st)
        return rw is None or name not in rw[1]

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
            chain.append(f"{self.modules[key[0]].statements[key[1]].id} ({reason})")
            key = reason.parent
        return chain

    # -- import rewriting ------------------------------------------------------------------
    def rewrite(self, st: Statement) -> Rewrite | None:
        """Replacement for a kept import that binds unused names, else None.

        ``from m import a, b`` with only ``a`` used becomes ``from m import a``. If no name is
        used the statement only runs for its side effects and becomes ``import m as _shakeout_N``.
        Imports of external modules are never rewritten (a missing name must still fail), a
        dropped submodule with side effects is still imported, and plain ``import x`` of a module
        with side effects stays verbatim so traceback text stays exact.
        """
        key = (st.module, st.index)
        if key not in self._rewrites:
            self._rewrites[key] = self._compute_rewrite(st)
        return self._rewrites[key]

    def _compute_rewrite(self, st: Statement) -> Rewrite | None:
        key = (st.module, st.index)
        if (
            not self.options.shake
            or key not in self.live
            or st.module in self.whole
            or not is_rewritable_import(st)
        ):
            return None
        node = st.nodes[0]
        used = self.used.get(key, set())
        if isinstance(node, ast.ImportFrom):
            return self._rewrite_from(st, node, used)
        if isinstance(node, ast.Import):
            return self._rewrite_import(node, used)
        return None  # pragma: no cover - is_rewritable_import admits only imports

    def _rewrite_from(self, st: Statement, node: ast.ImportFrom, used: set[str]) -> Rewrite | None:
        mod = self.modules[st.module]
        base = ImportContext(mod.name, mod.is_package).absolute(node.level, node.module)
        if base is None or base not in self.modules:
            return None
        keep: list[ast.alias] = []
        dropped: set[str] = set()
        for alias in node.names:
            bound = alias.asname or alias.name
            sub = self.modules.get(f"{base}.{alias.name}")
            if bound in used or (sub is not None and sub.has_effects):
                keep.append(alias)
            else:
                dropped.add(bound)
        if not dropped:
            return None
        new: ast.stmt
        if keep:
            new = ast.ImportFrom(module=node.module, names=keep, level=node.level)
        else:
            new = ast.Import(names=[ast.alias(name=base, asname=f"_shakeout_import_{st.start}")])
        return (_annotated(new, node), frozenset(dropped))

    def _rewrite_import(self, node: ast.Import, used: set[str]) -> Rewrite | None:
        keep: list[ast.alias] = []
        dropped: set[str] = set()
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            effectful = any(
                self.modules[p].has_effects
                for p in module_prefixes(alias.name)
                if p in self.modules
            )
            if bound in used or alias.name not in self.modules or effectful:
                keep.append(alias)
            else:
                dropped.add(bound)
        if not dropped:
            return None
        if not keep:
            return ("pass  # shakeout: removed " + ast.unparse(node), frozenset(dropped))
        return (_annotated(ast.Import(names=keep), node), frozenset(dropped))


def _annotated(new: ast.stmt, old: ast.stmt) -> str:
    return f"{ast.unparse(new)}  # shakeout: was {ast.unparse(old)!r}"
