"""No git fetch may be reachable from a `seed._lock(...)` body (witness 152605).

A static guard over the package source. For every `with ..._lock(...)` block that
takes the seed lock, collect the functions it calls, follow calls by bare name
through package-defined functions, and fail if any reached function runs a
`git fetch` (argv containing the literal "fetch", or a call passing "fetch" as a
positional argument to a git helper).

Known debt is an explicit allowlist, not silence: each entry names the lock
caller, the function and the callee that reaches the fetch, so a new path fails
loudly and removing the old one forces the allowlist to shrink. Reopen trigger
for the entry below: a long seed-lock hold whose holder tree contains a git
fetch child (witness 152605).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "mishe_tauftauf"

# (file relative to PACKAGE, enclosing function, callee name) that reaches a git fetch
# while the seed lock is held. Known and dated; see module docstring for the trigger.
KNOWN_FETCH_UNDER_LOCK: set[tuple[str, str, str]] = set()   # wall.tick, the one entry, was deleted


@dataclass(frozen=True)
class Site:
    file: str
    function: str
    lineno: int


def _modules() -> dict[str, ast.Module]:
    return {str(p.relative_to(PACKAGE)): ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for p in sorted(PACKAGE.rglob("*.py"))}


def _walk_no_nested_defs(node: ast.AST):
    """Yield descendants of node without entering nested function or class bodies."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _walk_no_nested_defs(child)


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _is_git_fetch(call: ast.Call) -> bool:
    # argv form: subprocess.run(["git", ..., "fetch", ...]) or a tuple of the same
    for arg in call.args:
        if isinstance(arg, (ast.List, ast.Tuple)):
            items = [e.value for e in arg.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if "git" in items and "fetch" in items:
                return True
    # helper form: git("fetch", "--quiet", ...)
    first = call.args[0] if call.args else None
    return isinstance(first, ast.Constant) and first.value == "fetch" and _callee_name(call) == "git"


def _function_index(modules: dict[str, ast.Module]):
    """Map bare function name -> list of (file, callees, direct_fetch)."""
    index: dict[str, list[tuple[str, set[str], bool]]] = {}
    for file, tree in modules.items():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
                callees = {n for n in (_callee_name(c) for c in calls) if n}
                direct = any(_is_git_fetch(c) for c in calls)
                index.setdefault(node.name, []).append((file, callees, direct))
    return index


def _reaching_names(index) -> set[str]:
    """Fixpoint: package function names from which a git fetch is reachable by bare-name calls.

    Only names with exactly one package definition propagate. A bare name such as
    `read`, `write` or `save` is defined in many modules and would otherwise connect
    every caller to every fetch; that over-approximation made the first run flag
    unrelated lock bodies. Limit: a fetch hidden behind an ambiguous name is not
    followed; the direct-fetch positive control still catches the fetch itself.
    """
    unique = {name: defs for name, defs in index.items() if len(defs) == 1}
    reaching = {name for name, defs in unique.items() if any(direct for _f, _c, direct in defs)}
    changed = True
    while changed:
        changed = False
        for name, defs in unique.items():
            if name not in reaching and any(callees & reaching for _f, callees, _d in defs):
                reaching.add(name)
                changed = True
    return reaching


def _lock_sites(modules: dict[str, ast.Module]):
    """Yield (file, enclosing function, with-node) for each seed._lock(...) block."""
    for file, tree in modules.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in _walk_no_nested_defs(fn):
                if not isinstance(node, ast.With):
                    continue
                for item in node.items:
                    expr = item.context_expr
                    if isinstance(expr, ast.Call) and _callee_name(expr) == "_lock":
                        yield file, fn.name, node


def find_fetch_under_seed_lock(modules: dict[str, ast.Module] | None = None) -> set[tuple[str, str, str]]:
    modules = modules if modules is not None else _modules()
    index = _function_index(modules)
    reaching = _reaching_names(index)
    hits: set[tuple[str, str, str]] = set()
    for file, fn_name, with_node in _lock_sites(modules):
        if file != "site_lock.py" and not any(word in ast.unparse(with_node.items[0].context_expr)
                                               for word in ("site_lock", "task_lock", "_lock(")):
            # only the seed lock is in scope; other modules have their own _lock
            continue
        for node in with_node.body:
            for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
                name = _callee_name(call)
                if name and name in reaching:
                    hits.add((Path(file).name, fn_name, name))
    return hits


def test_detector_sees_the_known_fetch_site():
    """Positive control: the fetch is really in the package, so the guard has something to find."""
    index = _function_index(_modules())
    assert any(direct for _f, _c, direct in index.get("_repo_push_freshness", [])), (
        "detector no longer finds the git fetch in _repo_push_freshness; the guard is blind")


def test_detector_flags_a_synthetic_fetch_under_seed_lock(tmp_path):
    """Controlled failure: a new fetch behind a seed._lock block must be reported."""
    synthetic = {
        "probe.py": ast.parse(
            "import subprocess\n"
            "def fetch_now(home):\n"
            "    subprocess.run(['git', '-C', home, 'fetch', 'origin', 'main'])\n"
            "def tick(home):\n"
            "    with seed._lock(home):\n"
            "        fetch_now(home)\n"
        ),
    }
    assert find_fetch_under_seed_lock(synthetic) == {("probe.py", "tick", "fetch_now")}


def test_detector_ignores_fetch_outside_seed_lock():
    synthetic = {
        "probe.py": ast.parse(
            "import subprocess\n"
            "def fetch_now(home):\n"
            "    subprocess.run(['git', '-C', home, 'fetch', 'origin', 'main'])\n"
            "def tick(home):\n"
            "    fetch_now(home)\n"
            "    with seed._lock(home):\n"
            "        pass\n"
        ),
    }
    assert find_fetch_under_seed_lock(synthetic) == set()


def test_no_git_fetch_reachable_from_seed_lock_beyond_known_debt():
    hits = find_fetch_under_seed_lock()
    new = hits - KNOWN_FETCH_UNDER_LOCK
    assert not new, (
        "git fetch reachable from a seed._lock body (move it outside the lock, or justify "
        f"and allowlist with a reopen trigger): {sorted(new)}")


def test_known_debt_is_still_exact():
    """Fail when the allowlisted path disappears, so the allowlist cannot go stale silently."""
    hits = find_fetch_under_seed_lock()
    gone = KNOWN_FETCH_UNDER_LOCK - hits
    assert not gone, f"allowlisted fetch-under-lock no longer present; remove entry: {sorted(gone)}"
