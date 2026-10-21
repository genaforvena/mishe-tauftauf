#!/usr/bin/env python3
"""Detector: a promoted release tree must have a complete local-import closure.

A release tree is cut from *committed* bytes.  If a committed module imports a
sibling that was never committed, the checkout still imports cleanly -- the
sibling's bytes are sitting untracked in the working tree -- and the failure
first appears in the promoted runtime, on the code path that reaches the
import.

Measured on this plant on 2026-10-21 (body-research wake 80486, confirmed
independently by discover wake 81164):

* commit ``d4f4592`` added ``seed.py:636`` -> ``from .mind_loop import select``
* commit ``2e88d94`` added the ``seed loop`` subparser in ``cli.py`` that calls it
* ``src/mishe_tauftauf/mind_loop.py`` is **still untracked**, so it is in no
  release tree

Consequence, reproduced in the HEAD release tree ``b58b03d``:

    $ PYTHONPATH=src python -m mishe_tauftauf seed loop --slug body-research --kind internal
    ModuleNotFoundError: No module named 'mishe_tauftauf.mind_loop'

The failure is loud and CLI-only, which is exactly why nothing noticed: no unit
test and no mind launcher references ``seed loop``, so the suite is green and
CI (``check_parsing.py`` + ``pytest``) is green over the same hole.

This check asks a property question about a *tree*, not about the code that
produced it:

* **unresolved** -- a committed module imports a sibling that the tree does
  not contain.  The promoted runtime raises ``ModuleNotFoundError`` there.
* **reachable** -- the unresolved import is on a CLI subcommand a user or a
  launcher can name, so the defect is *exercisable* rather than theoretical.

An unresolved import that no CLI surface can reach is reported as latent, not
as a failure, because it cannot break a promoted runtime on its own.  A
reachable one is a defect in the tree.

The tree under test is the *committed* bytes, read through ``git archive`` when
a repository is given, so untracked working-tree files can never satisfy an
import.  That is the whole point: the question is what a release tree
contains, not what the checkout happens to hold.

Exit status:
  0  every local import in the tree resolves, or the only unresolved ones are
     unreachable from any CLI surface
  2  a reachable local import does not resolve in the tree
  3  the question cannot be decided: no tree, no git, or the tree cannot be read
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tempfile
from pathlib import Path

GIT = "/usr/bin/git"


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="import-closure-")
    archive = subprocess.run([GIT, "-C", str(repo), "archive", "HEAD"],
                             capture_output=True)
    if archive.returncode:
        return None, archive.stderr.decode("utf-8", "replace").strip()
    unpack = subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout,
                            capture_output=True)
    if unpack.returncode:
        return None, unpack.stderr.decode("utf-8", "replace").strip()
    return Path(tmp), ""


def _modules(tree: Path) -> set[str]:
    pkg = tree / "src" / "mishe_tauftauf"
    if not pkg.is_dir():
        return set()
    return {p.stem for p in pkg.glob("*.py")}


def _imports(tree: Path) -> list[tuple[str, int, str, str]]:
    """(module stem, line, importer file, enclosing function) for local imports.

    The enclosing function matters: a bad import inside ``seed.loop`` is only
    reachable if the CLI dispatches into ``seed.loop`` specifically.
    """
    pkg = tree / "src" / "mishe_tauftauf"
    found: list[tuple[str, int, str, str]] = []
    if not pkg.is_dir():
        return found
    for path in sorted(pkg.glob("*.py")):
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            print(f"parse-error {path.name}: {exc}", file=sys.stderr)
            continue
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    continue
                if node.level > 1:
                    found.append((f"<level-{node.level}>", node.lineno, path.name, "<module>"))
                    continue
                if node.module:
                    found.append((node.module.split(".")[0], node.lineno, path.name,
                                  _enclosing(module, node)))
                else:
                    for alias in node.names:
                        found.append((alias.name.split(".")[0], node.lineno, path.name,
                                      _enclosing(module, node)))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("mishe_tauftauf."):
                        parts = alias.name.split(".")
                        if len(parts) > 2:
                            found.append((f"<level-2:{alias.name}>", node.lineno,
                                          path.name, "<module>"))
                        else:
                            found.append((parts[1], node.lineno, path.name,
                                          _enclosing(module, node)))
    return found


def _enclosing(module: ast.Module, target: ast.AST) -> str:
    """The name of the function that contains ``target``, or '<module>'."""
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if child is target:
                    return node.name
    return "<module>"


def _cli_surfaces(tree: Path) -> list[str]:
    """The CLI subcommands a user can name, as 'seed loop' style paths."""
    cli = tree / "src" / "mishe_tauftauf" / "cli.py"
    if not cli.is_file():
        return []
    try:
        module = ast.parse(cli.read_text(encoding="utf-8"), filename=str(cli))
    except (OSError, SyntaxError):
        return []
    surfaces: list[str] = []
    for node in ast.walk(module):
        # p = seed.add_parser("loop") / tmux.add_parser("start")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "add_parser" or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            name = first.value
            owner = node.func.value
            owner_name = owner.id if isinstance(owner, ast.Name) else None
            if owner_name == "seed":
                surfaces.append("seed " + name)
            elif owner_name == "tmux":
                surfaces.append("tmux " + name)
            elif owner_name == "sub":
                surfaces.append(name)
    return sorted(set(surfaces))


def _dispatched_modules(tree: Path) -> set[str]:
    """Modules the CLI's dispatch sites call into, by attribute name.

    ``cli.py`` dispatches ``seed loop`` to ``seed.loop(...)``, so a bad import
    inside ``seed.py`` is reachable from a named subcommand.  The link the
    plant's failure needs is exactly this one: the CLI names a module, and
    that module holds an import the tree does not satisfy.
    """
    pkg = tree / "src" / "mishe_tauftauf"
    cli = pkg / "cli.py"
    if not cli.is_file():
        return set()
    try:
        module = ast.parse(cli.read_text(encoding="utf-8"), filename=str(cli))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name):
                names.add(owner.id)
            elif isinstance(owner, ast.Attribute):
                names.add(owner.attr)
    return names


def _reachable_from_cli(tree: Path, importer: str, function: str) -> bool:
    """Is the bad import reachable from a named CLI subcommand?

    Three links must all hold, and all three are read from the tree rather
    than assumed:

    * the importer's module defines the function that holds the bad import;
    * the CLI dispatches a subcommand into *that function* (``seed.loop(...)``,
      not merely ``seed.init(...)``);
    * that dispatch sits in a handler a registered subcommand points at.

    The middle link is what separates this plant's two states.  The running pin
    holds the same unresolved import but no CLI path calls ``seed.loop``, so
    the hole there is latent -- it cannot raise on any command a user can
    name.  The committed head *does* dispatch ``seed loop`` to ``seed.loop``,
    so the same hole is reachable and breaks the promoted runtime.
    """
    pkg = tree / "src" / "mishe_tauftauf"
    module_path = pkg / f"{importer}.py"
    if not module_path.is_file():
        return False
    cli = pkg / "cli.py"
    if not cli.is_file():
        return False
    try:
        cli_module = ast.parse(cli.read_text(encoding="utf-8"), filename=str(cli))
    except (OSError, SyntaxError):
        return False

    handlers = _handler_names(cli_module)
    if not handlers:
        return False
    for node in ast.walk(cli_module):
        if not (isinstance(node, ast.FunctionDef) and node.name in handlers):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if not isinstance(func, ast.Attribute) or func.attr != function:
                continue
            owner = func.value
            if isinstance(owner, ast.Name) and owner.id == importer:
                return True
            if isinstance(owner, ast.Attribute) and owner.attr == importer:
                return True
    return False


def _handler_names(cli_module: ast.Module) -> set[str]:
    """The functions the CLI's registered subcommands point at."""
    handlers: set[str] = set()
    for node in ast.walk(cli_module):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "set_defaults":
                continue
            for kw in node.keywords:
                if kw.arg == "func" and isinstance(kw.value, ast.Name):
                    handlers.add(kw.value.id)
    return handlers


def _pending_holes(repo: Path) -> list[tuple[str, str, int]]:
    """Imports a *pending* commit would add without the module it names.

    The forward direction matters as much as the backward one.  A tracked file
    that is edited to import a sibling which is only an untracked working-tree
    file imports cleanly in the checkout, passes the suite, and breaks the
    promoted runtime the moment it is committed.

    Measured on this plant on 2026-10-21: ``inference_loop.py`` (tracked,
    modified) imports ``native_pane`` (untracked).  Committing it as it stands
    would open the same class of hole that ``seed.py``'s ``mind_loop`` import
    already has.

    Returns (importer, missing module, line) for each such import.
    """
    pkg = repo / "src" / "mishe_tauftauf"
    if not pkg.is_dir():
        return []
    try:
        untracked = subprocess.run(
            [GIT, "-C", str(repo), "ls-files", "--others", "--exclude-standard",
             "--", "src/mishe_tauftauf"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    untracked_stems = {Path(line).stem for line in untracked.splitlines()
                       if line.endswith(".py")}
    if not untracked_stems:
        return []

    try:
        modified = subprocess.run(
            [GIT, "-C", str(repo), "diff", "--name-only", "--", "src/mishe_tauftauf"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    modified_stems = {Path(line).stem for line in modified.splitlines()
                      if line.endswith(".py")}

    holes: list[tuple[str, str, int]] = []
    for importer_stem in sorted(modified_stems):
        path = pkg / f"{importer_stem}.py"
        if not path.is_file():
            continue
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(module):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module:
                    names.append(node.module.split(".")[0])
                else:
                    names.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("mishe_tauftauf."):
                        names.append(alias.name.split(".")[1])
            for name in names:
                if name in untracked_stems:
                    holes.append((f"{importer_stem}.py", name, node.lineno))
    return holes


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True,
                        help="site home (kept for the observer-checks runner contract)")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository; the committed bytes of HEAD are tested")
    parser.add_argument("--tree", type=Path, default=None,
                        help="an explicit tree to test instead of the committed bytes")
    parser.add_argument("--forward", action="store_true",
                        help="also report imports a pending commit would add without "
                             "the module it names (tracked edit importing an untracked module)")
    args = parser.parse_args(argv)

    tmp: str | None = None
    if args.tree:
        tree = args.tree.resolve()
    elif args.repo:
        tree, error = _committed_tree(args.repo.resolve())
        if tree is None:
            print("cannot read committed bytes: " + error)
            return 3
        tmp = str(tree)
    else:
        print("no tree and no repo given")
        return 3

    try:
        present = _modules(tree)
        imports = _imports(tree)
        if not present:
            print(f"no package at {tree / 'src' / 'mishe_tauftauf'}")
            return 3
        unresolved: dict[str, list[tuple[str, int, str, str]]] = {}
        for stem, line, importer, function in imports:
            if stem not in present:
                unresolved.setdefault(stem, []).append((importer, line, function))

        print(f"tree={tree}")
        print(f"modules present={len(present)} local imports checked={len(imports)}")

        reachable = []
        latent = []
        for stem, sites in sorted(unresolved.items()):
            hit = None
            for importer, line, function in sites:
                module_name = importer.removesuffix(".py")
                if function == "<module>":
                    continue
                if _reachable_from_cli(tree, module_name, function):
                    hit = (importer, line, function)
                    break
            if hit:
                reachable.append((stem, sites, hit))
            else:
                latent.append((stem, sites))

        for stem, sites in latent:
            where = ", ".join(f"{f}:{n} in {fn}()" for f, n, fn in sorted(sites))
            print(f"latent: {stem} <- {where} (no CLI surface dispatches it)")

        pending: list[tuple[str, str, int]] = []
        if args.forward and args.repo:
            pending = _pending_holes(args.repo.resolve())
            for importer, missing, line in pending:
                print(f"pending: {importer}:{line} imports {missing}, which is an "
                      f"untracked working-tree file; committing the edit opens the "
                      f"same hole in every promoted runtime")

        if not reachable and not pending:
            if not unresolved:
                print("OK every local import resolves inside the tree")
            else:
                print("OK every unresolved local import is unreachable from a CLI surface")
            return 0
        if reachable:
            print("INCOMPLETE — reachable local imports that do not resolve in this tree:")
            for stem, sites, hit in reachable:
                where = ", ".join(f"{f}:{n} in {fn}()" for f, n, fn in sorted(sites))
                print(f"  {stem}  <- {where}")
            print("FAIL the committed tree dispatches a CLI subcommand whose module is absent; "
                  "every promoted runtime raises ModuleNotFoundError on that path")
        if pending:
            print("FAIL a pending edit imports a module that exists only as untracked bytes; "
                  "committing it opens the same hole in every promoted runtime")
        return 2
    finally:
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
