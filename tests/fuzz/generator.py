"""Hypothesis strategy producing random, terminating, multi-module Python programs.

Every function prints a unique token when called, so any behavior change from removing code is
observable on stdout. Calls only go "forward" in a global item order and module-level imports
only go to higher-numbered modules, which guarantees termination and an import DAG; function-local
imports may point anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hypothesis import strategies as st

KINDS = (
    "func",
    "func",
    "func",
    "const",
    "registered",
    "plugin",
    "plain_class",
    "effect",
    "dynamic",
    "reexport",
)


@dataclass
class Item:
    module: int
    index: int
    kind: str
    calls: list[tuple[int, int, str]] = field(
        default_factory=list[tuple[int, int, str]]
    )  # (module, index, style)
    value: int = 0

    @property
    def name(self) -> str:
        prefix = {"const": "C", "plugin": "P", "plain_class": "K"}.get(self.kind, "f")
        return f"{prefix}{self.module}_{self.index}"


@dataclass
class Program:
    modules: list[list[Item]]
    main_uses: list[tuple[int, int, str]]  # (module, index, import style)

    def item(self, m: int, i: int) -> Item:
        return self.modules[m][i]


@st.composite
def programs(draw: st.DrawFn) -> Program:
    n_modules = draw(st.integers(1, 4))
    modules: list[list[Item]] = []
    for m in range(n_modules):
        kinds = draw(st.lists(st.sampled_from(KINDS), min_size=1, max_size=6))
        modules.append([Item(m, i, k, value=draw(st.integers(0, 9))) for i, k in enumerate(kinds)])
    callable_kinds = {"func", "registered", "dynamic", "plain_class", "plugin"}
    order = [(m, i) for m in range(n_modules) for i in range(len(modules[m]))]
    for pos, (m, i) in enumerate(order):
        item = modules[m][i]
        later = [
            (mm, ii)
            for (mm, ii) in order[pos + 1 :]
            if modules[mm][ii].kind in callable_kinds | {"const"}
        ]
        if item.kind in ("func", "registered", "plain_class", "dynamic") and later:
            for target in draw(st.lists(st.sampled_from(later), max_size=3, unique=True)):
                style = "local" if target[0] == m else draw(st.sampled_from(["attr", "from_local"]))
                item.calls.append((*target, style))
        if item.kind == "reexport":
            others = [
                (mm, ii)
                for (mm, ii) in order
                if mm > m and modules[mm][ii].kind in callable_kinds | {"const"}
            ]
            if others:
                item.calls.append((*draw(st.sampled_from(others)), "reexport"))
            else:
                item.kind = "const"
    candidates = [(m, i) for (m, i) in order if modules[m][i].kind != "effect"]
    uses = (
        draw(st.lists(st.sampled_from(candidates), max_size=5, unique=True)) if candidates else []
    )
    main_uses = [(m, i, draw(st.sampled_from(["attr", "from"]))) for (m, i) in uses]
    return Program(modules, main_uses)


def _use_expr(prog: Program, m: int, i: int, via: str) -> str:
    item = prog.item(m, i)
    if item.kind == "const":
        return via
    if item.kind == "reexport":
        if not item.calls:
            return via
        tm, ti, _ = item.calls[0]
        return _use_expr(prog, tm, ti, via)
    if item.kind in ("plain_class", "plugin"):
        return f"{via}().run()"
    return f"{via}()"


REG_MODULE = """\
REGISTRY = {}


def register(fn):
    REGISTRY[fn.__name__] = fn
    return fn


class Plugin:
    seen = []

    def __init_subclass__(cls):
        Plugin.seen.append(cls.__name__)

    def run(self):
        return 0
"""


def _calls(prog: Program, it: Item) -> tuple[list[str], list[str]]:
    """Expressions an item's body evaluates, and the local imports they need."""
    body_calls: list[str] = []
    local_imports: list[str] = []
    for tm, ti, style in it.calls:
        target = prog.item(tm, ti)
        if style == "local":
            body_calls.append(_use_expr(prog, tm, ti, target.name))
        elif style == "attr":
            body_calls.append(_use_expr(prog, tm, ti, f"m{tm}.{target.name}"))
        elif style == "from_local":
            local_imports.append(f"    from m{tm} import {target.name}")
            body_calls.append(_use_expr(prog, tm, ti, target.name))
    return body_calls, local_imports


def _render_callable(it: Item, total: str, local_imports: list[str]) -> list[str]:
    if it.kind == "plain_class":
        return [
            f"class {it.name}:",
            "    def run(self):",
            *("    " + li for li in local_imports),
            f"        print({it.name!r})",
            f"        return {total}",
        ]
    deco = ["@reg.register"] if it.kind == "registered" else []
    if it.kind == "dynamic":
        ret = f"    return globals()[{it.name!r}].__name__.count('_') + {total}"
    else:
        ret = f"    return {total}"
    return [*deco, f"def {it.name}():", *local_imports, f"    print({it.name!r})", ret]


def _render_item(prog: Program, m: int, it: Item) -> list[str]:
    if it.kind in ("func", "registered", "dynamic", "plain_class"):
        body_calls, local_imports = _calls(prog, it)
        total = " + ".join([str(it.value), *(f"_n({c})" for c in body_calls)])
        return _render_callable(it, total, local_imports)
    if it.kind == "plugin":
        return [f"class {it.name}(reg.Plugin):", "    pass"]
    if it.kind == "effect":
        return [f"print('effect m{m} {it.index}')"]
    if it.kind == "reexport":
        tm, ti, _ = it.calls[0]
        return [f"from m{tm} import {prog.item(tm, ti).name} as {it.name}"]
    return [f"{it.name} = {it.value}"]  # const


def _render_module(prog: Program, m: int, items: list[Item]) -> str:
    top_imports = sorted({tm for it in items for (tm, _, style) in it.calls if style == "attr"})
    lines = ["import reg", "def _n(v):\n    return v if isinstance(v, int) else 0\n\n"]
    lines += [f"import m{k}" for k in top_imports] + [""]
    for it in items:
        lines += [*_render_item(prog, m, it), "", ""]
    return "\n".join(lines).rstrip() + "\n"


def _render_main(prog: Program) -> str:
    main = ["import reg"]
    calls: list[str] = []
    for m, i, style in prog.main_uses:
        item = prog.item(m, i)
        if style == "attr":
            main.append(f"import m{m}")
            calls.append(_use_expr(prog, m, i, f"m{m}.{item.name}"))
        else:
            main.append(f"from m{m} import {item.name}")
            calls.append(_use_expr(prog, m, i, item.name))
    main.append("")
    main.extend(f"print('result', {c})" for c in calls)
    main.append("for _k in sorted(reg.REGISTRY):\n    print('registered', _k, reg.REGISTRY[_k]())")
    main.append("print('plugins', sorted(reg.Plugin.seen))")
    return "\n".join(main) + "\n"


def render(prog: Program) -> dict[str, str]:
    """Source files of ``prog``: reg.py, m0.py ... and main.py."""
    files = {"reg.py": REG_MODULE}
    for m, items in enumerate(prog.modules):
        files[f"m{m}.py"] = _render_module(prog, m, items)
    files["main.py"] = _render_main(prog)
    return files
