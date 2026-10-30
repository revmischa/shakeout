"""Side effects of class definitions that come from their bases.

Subclassing runs ``__init_subclass__`` and metaclass code of every base, which is how plugin
registries work. A class is *registering* if it defines ``__init_subclass__``, has a metaclass,
or inherits from a registering class; defining a subclass of a registering class is a side effect.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from shakeout.analysis.effects import EffectRules
from shakeout.analysis.registry import PURE_BASE_MODULES, PURE_BASES
from shakeout.analysis.scopes import attribute_chain
from shakeout.model import NameTarget

if TYPE_CHECKING:
    from collections.abc import Callable

    from shakeout.graph import Resolver, StmtKey
    from shakeout.model import Module

Verdict = tuple[str | None, bool]  # (impurity reason from bases, is registering)


class ClassEffects:
    """Classifies class statements by what their bases do when subclassed."""

    def __init__(
        self,
        modules: dict[str, Module],
        resolver: Resolver,
        qualify: Callable[[Module, ast.expr], str | None],
        *,
        pure_calls: frozenset[str],
        assume_pure_bases: bool,
    ) -> None:
        """Create an analyzer over ``modules``; ``qualify`` names expressions per module."""
        self.modules = modules
        self.resolver = resolver
        self.qualify = qualify
        self.pure_calls = pure_calls
        self.assume_pure_bases = assume_pure_bases
        self._memo: dict[StmtKey, Verdict] = {}

    def classify_all(self) -> None:
        """Set ``impure`` on every class statement whose bases make defining it observable."""
        for mod in self.modules.values():
            for st in mod.statements:
                if st.kind == "class" and not st.impure:
                    st.impure, _ = self.verdict((mod.name, st.index), frozenset())

    def verdict(self, key: StmtKey, active: frozenset[StmtKey]) -> Verdict:
        """Impurity reason and registering flag for the class statement ``key``."""
        if key in self._memo:
            return self._memo[key]
        if key in active:
            return ("cyclic class hierarchy", True)
        mod = self.modules[key[0]]
        node = mod.statements[key[1]].nodes[0]
        if not isinstance(node, ast.ClassDef):  # pragma: no cover - callers check kind
            return (None, False)
        registering = _declares_registration(node)
        impure: str | None = None
        for base in node.bases:
            reason = self._base_reason(mod, base, active | {key})
            if reason:
                impure = impure or reason
                registering = True
        self._memo[key] = (impure, registering)
        return self._memo[key]

    def _base_reason(self, mod: Module, base: ast.expr, active: frozenset[StmtKey]) -> str | None:
        text = ast.unparse(base)
        expr = base.value if isinstance(base, ast.Subscript) else base
        chain = attribute_chain(expr)
        if chain is None:
            rules = EffectRules(lambda e: self.qualify(mod, e), self.pure_calls)
            return None if rules.pure_expr(base) else f"base {text!r} may have side effects"
        qualified = self.qualify(mod, expr)
        if qualified is not None and self._is_external(qualified):
            return self._external_base_reason(qualified, text)
        res = self.resolver.resolve(mod.name, NameTarget(chain[0]), chain[1])
        if not res.terminal:
            return None if self.assume_pure_bases else f"base {text!r} cannot be resolved"
        for tkey in sorted(res.terminal):
            tst = self.modules[tkey[0]].statements[tkey[1]]
            if tst.kind == "class" and isinstance(tst.nodes[0], ast.ClassDef):
                if self.verdict(tkey, active)[1]:
                    return f"base {text!r} registers subclasses (__init_subclass__/metaclass)"
            elif not self.assume_pure_bases:
                return f"base {text!r} is not a plain class definition"
        return None

    def _external_base_reason(self, qualified: str, text: str) -> str | None:
        known_pure = (
            qualified.startswith("builtins.")
            or qualified in PURE_BASES
            or qualified.rpartition(".")[0] in PURE_BASE_MODULES
        )
        if known_pure or self.assume_pure_bases:
            return None
        return f"base {text!r} comes from outside the bundle and may register subclasses"

    def _is_external(self, qualified: str) -> bool:
        parts = qualified.split(".")
        return not any(".".join(parts[:i]) in self.modules for i in range(len(parts), 0, -1))


def _declares_registration(node: ast.ClassDef) -> bool:
    return any(k.arg == "metaclass" for k in node.keywords) or any(
        isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and b.name == "__init_subclass__"
        for b in node.body
    )
