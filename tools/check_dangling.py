#!/usr/bin/env python3
"""Report names and imports that resolve to nothing in src/mishe_tauftauf.

A stand-in for a linter after deleting modules or functions: global names read
in any scope that the module never binds (and are not builtins), and
``from .x import y`` where ``x`` is missing or does not define ``y``.
"""
from __future__ import annotations

import ast
import builtins
import pathlib
import symtable
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "mishe_tauftauf"
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__path__"}


def module_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).partition(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def scan(table, bound, path, out):
    for sym in table.get_symbols():
        if sym.is_referenced() and (sym.is_global() or (table.get_type() == "module" and not sym.is_assigned())):
            if sym.get_name() not in bound and sym.get_name() not in BUILTINS:
                out.append(f"{path.relative_to(ROOT.parent.parent)}: undefined name {sym.get_name()!r} in {table.get_name()}")
    for child in table.get_children():
        scan(child, bound, path, out)


def attribute_problems(modules, defined) -> list[str]:
    """``module.attr`` where module is a sibling and attr is not defined at its top level."""
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        aliases = {}                                  # local name -> sibling module
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
                for a in node.names:
                    if a.name in modules:
                        aliases[a.asname or a.name] = a.name
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "mishe_tauftauf":
                for a in node.names:
                    if a.name in modules:
                        aliases[a.asname or a.name] = a.name
        shadowed = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id not in shadowed
                    and node.value.id in aliases and isinstance(node.ctx, ast.Load)):
                target = aliases[node.value.id]
                if target in defined and node.attr not in defined[target] and not node.attr.startswith("__"):
                    out.append(f"{path.relative_to(ROOT.parent.parent)}:{node.lineno}: {node.value.id}.{node.attr} "
                               f"is not defined in {target}")
    return out


def main() -> int:
    problems = []
    modules = {p.stem: p for p in ROOT.glob("*.py")}
    defined = {name: module_names(path) for name, path in modules.items()}
    for path in sorted(ROOT.rglob("*.py")):
        text = path.read_text()
        try:
            scan(symtable.symtable(text, str(path), "exec"), module_names(path), path, problems)
        except SyntaxError as exc:
            problems.append(f"{path}: syntax error {exc}")
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and path.parent == ROOT:
                if node.module is None:
                    for a in node.names:
                        if a.name not in modules and not (ROOT / a.name).is_dir():
                            problems.append(f"{path.name}:{node.lineno}: from . import {a.name}: no such module")
                elif "." in node.module:
                    if not (ROOT / (node.module.replace(".", "/") + ".py")).exists():
                        problems.append(f"{path.name}:{node.lineno}: from .{node.module}: no such module")
                elif node.module not in modules and not (ROOT / node.module).is_dir():
                    problems.append(f"{path.name}:{node.lineno}: from .{node.module}: no such module")
                else:
                    for a in node.names:
                        if node.module in defined and a.name != "*" and a.name not in defined[node.module]:
                            problems.append(f"{path.name}:{node.lineno}: from .{node.module} import {a.name}: not defined there")
    problems += attribute_problems(modules, defined)
    for p in problems:
        print(p)
    print(f"{len(problems)} dangling reference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
