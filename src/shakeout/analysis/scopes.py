"""Scope-correct collection of references, bindings and imports for one top-level statement.

Python resolves a name inside a function to a local if it is bound anywhere in that function
(assignment, parameter, import, ``for`` target, ``with``/``except`` alias, ``match`` capture,
``del``, walrus) unless declared ``global``. Class bodies do not create scopes visible to the
methods inside them. Comprehensions get their own scope, except that their first iterable is
evaluated in the enclosing scope and walrus targets bind in the enclosing function.

Over-approximating references is always safe (it only keeps more code). Under-approximating is a
soundness bug, so every simplification here errs toward reporting more references.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from ..model import DynamicUse, FromTarget, ImportTarget, ModuleTarget, NameTarget, Ref

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
# Marker in a scope dict: the name is declared ``global`` here, so lookup must stop and resolve
# to the module namespace even if an enclosing function has a local of the same name.
GLOBAL_DECL = ModuleTarget("<global>")

_DYNAMIC_BUILTINS = {"globals": "globals", "vars": "vars", "locals": "locals", "eval": "eval",
                     "exec": "exec"}


@dataclass(frozen=True)
class ImportContext:
    module: str
    is_package: bool

    def absolute(self, level: int, module: str | None) -> str | None:
        """Resolve a possibly relative module name; None if it escapes the top-level package."""
        if level == 0:
            return module
        package = self.module if self.is_package else self.module.rpartition(".")[0]
        parts = package.split(".") if package else []
        if level - 1 >= len(parts) or not package:
            return None
        base_parts = parts[: len(parts) - (level - 1)]
        if module:
            base_parts.append(module)
        return ".".join(base_parts) or None


def module_prefixes(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def import_bindings(
    node: ast.Import | ast.ImportFrom, ctx: ImportContext
) -> tuple[list[tuple[str, ImportTarget]], set[str], str | None]:
    """Return (bound name -> target pairs, candidate module names loaded, star-import source)."""
    pairs: list[tuple[str, ImportTarget]] = []
    loads: set[str] = set()
    star: str | None = None
    if isinstance(node, ast.Import):
        for alias in node.names:
            loads.update(module_prefixes(alias.name))
            if alias.asname:
                pairs.append((alias.asname, ModuleTarget(alias.name)))
            else:
                top = alias.name.split(".")[0]
                pairs.append((top, ModuleTarget(top)))
        return pairs, loads, star
    base = ctx.absolute(node.level, node.module)
    if base is None:
        return pairs, loads, star
    loads.update(module_prefixes(base))
    for alias in node.names:
        if alias.name == "*":
            star = base
            continue
        loads.add(f"{base}.{alias.name}")  # may be a submodule; filtered later
        pairs.append((alias.asname or alias.name, FromTarget(base, alias.name)))
    return pairs, loads, star


def attribute_chain(node: ast.expr) -> tuple[str, tuple[str, ...]] | None:
    """``a.b.c`` -> ("a", ("b", "c")); None if the chain does not bottom out at a name."""
    attrs: list[str] = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return node.id, tuple(reversed(attrs))
    return None


def _arg_names(args: ast.arguments) -> Iterator[str]:
    for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        yield a.arg
    if args.vararg:
        yield args.vararg.arg
    if args.kwarg:
        yield args.kwarg.arg


def _type_param_names(node: ast.AST) -> Iterator[str]:
    for tp in getattr(node, "type_params", ()) or ():
        yield tp.name


class _LocalsFinder(ast.NodeVisitor):
    """Names bound in one function scope (not descending into nested scopes)."""

    def __init__(self, ctx: ImportContext) -> None:
        self.ctx = ctx
        self.bound: dict[str, ImportTarget | None] = {}
        self.globals: set[str] = set()

    def _bind(self, name: str, target: ImportTarget | None = None) -> None:
        if target is not None or name not in self.bound:
            self.bound[name] = target

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._bind(node.name)  # decorators/defaults contain no bindings worth tracking

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._bind(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def _visit_comp(self, node: ast.AST) -> None:
        # Only walrus targets escape a comprehension into the enclosing function.
        for sub in _walk_no_nested_funcs(node):
            if isinstance(sub, ast.NamedExpr):
                self._bind(sub.target.id)

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _visit_comp

    def visit_Import(self, node: ast.Import | ast.ImportFrom) -> None:
        pairs, _, _ = import_bindings(node, self.ctx)
        for name, target in pairs:
            self._bind(name, target)

    visit_ImportFrom = visit_Import

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self._bind(name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._bind(node.rest)
        self.generic_visit(node)


def _walk_no_nested_funcs(node: ast.AST) -> Iterator[ast.AST]:
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for child in ast.iter_child_nodes(cur):
            if not isinstance(child, (*_FUNCS, ast.ClassDef)):
                stack.append(child)


def function_scope(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, ctx: ImportContext
) -> tuple[dict[str, ImportTarget | None], set[str]]:
    """Local names of a function (name -> import target or None) and its ``global`` names."""
    finder = _LocalsFinder(ctx)
    body: Iterable[ast.AST] = [node.body] if isinstance(node, ast.Lambda) else node.body
    for stmt in body:
        finder.visit(stmt)  # type: ignore[arg-type]
    scope: dict[str, ImportTarget | None] = {name: None for name in _arg_names(node.args)}
    scope.update({name: None for name in _type_param_names(node)})
    for name, target in finder.bound.items():
        if target is not None or name not in scope:
            scope[name] = target
    for name in finder.globals:
        scope[name] = GLOBAL_DECL
    return scope, finder.globals


@dataclass
class Collected:
    refs: list[Ref] = field(default_factory=list[Ref])
    loads: set[str] = field(default_factory=set[str])
    eager_loads: set[str] = field(default_factory=set[str])
    required: set[str] = field(default_factory=set[str])
    star_sources: set[str] = field(default_factory=set[str])
    dynamic: list[DynamicUse] = field(default_factory=list[DynamicUse])
    file_uses: list[int] = field(default_factory=list[int])
    globals_declared: set[str] = field(default_factory=set[str])


class RefCollector(ast.NodeVisitor):
    """Collect references from one top-level statement, resolving local scopes."""

    def __init__(self, ctx: ImportContext) -> None:
        self.ctx = ctx
        self.out = Collected()
        self.scopes: list[dict[str, ImportTarget | None]] = []
        self.fn_depth = 0

    # -- helpers ---------------------------------------------------------------------------
    def _lookup(self, name: str) -> tuple[bool, ImportTarget | None]:
        for scope in reversed(self.scopes):
            if name in scope:
                target = scope[name]
                if target is GLOBAL_DECL:
                    return False, None
                return True, target
        return False, None

    def _ref(self, name: str, attrs: tuple[str, ...], line: int) -> None:
        found, target = self._lookup(name)
        if found and target is None:
            return
        base = target if found and target is not None else NameTarget(name)
        self.out.refs.append(Ref(base, attrs, line, self.fn_depth > 0))
        if name == "__file__" and not found:
            self.out.file_uses.append(line)

    def _visit_all(self, nodes: Iterable[ast.AST | None]) -> None:
        for n in nodes:
            if n is not None:
                self.visit(n)

    # -- names -----------------------------------------------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._ref(node.id, (), node.lineno)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain = attribute_chain(node)
        if chain is None:
            self.generic_visit(node)
            return
        root, attrs = chain
        found, _ = self._lookup(root)
        if not found and root == "sys" and attrs[:1] == ("modules",):
            self.out.dynamic.append(DynamicUse("sys.modules", node.lineno))
        self._ref(root, attrs, node.lineno)

    # -- calls with dynamic semantics ------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        chain = attribute_chain(func)
        if chain is not None:
            root, attrs = chain
            found, target = self._lookup(root)
            if (not attrs and not found and root in _DYNAMIC_BUILTINS
                    and not (root == "vars" and node.args)):
                self.out.dynamic.append(DynamicUse(_DYNAMIC_BUILTINS[root], node.lineno))
            is_dunder_import = not attrs and not found and root == "__import__"
            is_import_module = (attrs == ("import_module",) and (
                (not found and root == "importlib")
                or target == ModuleTarget("importlib"))) or (
                not attrs and root == "import_module" and (
                    not found or target == FromTarget("importlib", "import_module")))
            if is_dunder_import or is_import_module:
                self._dynamic_import(node)
        self.generic_visit(node)

    def _dynamic_import(self, node: ast.Call) -> None:
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if arg.value.startswith("."):
                self.out.dynamic.append(DynamicUse("import", node.lineno, arg.value))
                return
            name = arg.value
            self.out.loads.update(module_prefixes(name))
            self.out.required.update(module_prefixes(name))
            self.out.refs.append(Ref(ModuleTarget(name), (), node.lineno, self.fn_depth > 0))
            return
        prefix = ""
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
            left = arg.left
            if isinstance(left, ast.Constant) and isinstance(left.value, str):
                prefix = left.value
        elif isinstance(arg, ast.JoinedStr) and arg.values:
            first = arg.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                prefix = first.value
        kind = "import-prefix" if prefix.endswith(".") and len(prefix) > 1 else "import"
        self.out.dynamic.append(DynamicUse(kind, node.lineno, prefix))

    # -- scopes ----------------------------------------------------------------------------
    def _visit_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        args = node.args
        self._visit_all(args.defaults)
        self._visit_all(args.kw_defaults)
        if not isinstance(node, ast.Lambda):
            self._visit_all(node.decorator_list)
            for a in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
                if a is not None and a.annotation is not None:
                    self.visit(a.annotation)
            if node.returns is not None:
                self.visit(node.returns)
            for tp in getattr(node, "type_params", ()) or ():
                self.visit(tp)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        self._visit_signature(node)
        scope, declared = function_scope(node, self.ctx)
        self.out.globals_declared.update(declared)
        self.scopes.append(scope)
        self.fn_depth += 1
        if isinstance(node, ast.Lambda):
            self.visit(node.body)
        else:
            self._visit_all(node.body)
        self.fn_depth -= 1
        self.scopes.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _visit_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_all(node.decorator_list)
        self._visit_all(node.bases)
        self._visit_all(k.value for k in node.keywords)
        for tp in getattr(node, "type_params", ()) or ():
            self.visit(tp)
        self._visit_all(node.body)  # class scope is not visible to nested functions

    def _visit_comp(self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp) -> None:
        gens = node.generators
        self.visit(gens[0].iter)
        scope: dict[str, ImportTarget | None] = {}
        for gen in gens:
            for sub in ast.walk(gen.target):
                if isinstance(sub, ast.Name):
                    scope[sub.id] = None
        self.scopes.append(scope)
        for i, gen in enumerate(gens):
            if i:
                self.visit(gen.iter)
            self._visit_all(gen.ifs)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.scopes.pop()

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _visit_comp

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)

    # -- imports ---------------------------------------------------------------------------
    def visit_Import(self, node: ast.Import | ast.ImportFrom) -> None:
        _, loads, star = import_bindings(node, self.ctx)
        self.out.loads.update(loads)
        if isinstance(node, ast.Import):
            self.out.required.update(loads)
        else:
            base = self.ctx.absolute(node.level, node.module)
            if base is not None:
                self.out.required.update(module_prefixes(base))
        if self.fn_depth == 0:
            self.out.eager_loads.update(loads)
        if star is not None:
            self.out.star_sources.add(star)
            self.out.refs.append(Ref(ModuleTarget(star), (), node.lineno, self.fn_depth > 0))

    visit_ImportFrom = visit_Import


class _BindFinder(ast.NodeVisitor):
    """Module-level names bound by a top-level statement."""

    def __init__(self, ctx: ImportContext) -> None:
        self.ctx = ctx
        self.binds: dict[str, list[ImportTarget | None]] = {}

    def _bind(self, name: str, target: ImportTarget | None = None) -> None:
        targets = self.binds.setdefault(name, [])
        if target not in targets:
            targets.append(target)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._bind(node.name)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Global):
                for name in sub.names:
                    self._bind(name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._bind(node.name)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Global):
                for name in sub.names:
                    self._bind(name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_Import(self, node: ast.Import | ast.ImportFrom) -> None:
        pairs, _, _ = import_bindings(node, self.ctx)
        for name, target in pairs:
            self._bind(name, target)

    visit_ImportFrom = visit_Import

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._bind(node.rest)
        self.generic_visit(node)


def module_bindings(node: ast.stmt, ctx: ImportContext) -> dict[str, list[ImportTarget | None]]:
    finder = _BindFinder(ctx)
    finder.visit(node)
    return finder.binds


def collect(node: ast.stmt, ctx: ImportContext) -> Collected:
    collector = RefCollector(ctx)
    collector.visit(node)
    return collector.out
