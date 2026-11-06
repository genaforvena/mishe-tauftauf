"""A wake must not import package modules while it waits on or holds the dispatch lock.

A function-level import inside ``with supervisor.exclusive():`` runs after the
lock is requested, so a release link swapped during the wait can change which
bytes that import loads. Such imports belong at module level, where the
process has them before any lock is taken.
"""
import ast
from pathlib import Path

WAKE = Path(__file__).resolve().parents[2] / "src/mishe_tauftauf/wake.py"


def _is_lock_with(node):
    return isinstance(node, ast.With) and any(
        "exclusive" in ast.unparse(item.context_expr) for item in node.items
    )


def _package_imports(nodes):
    found = []
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.ImportFrom):
                if node.level > 0 or (node.module or "").startswith("mishe_tauftauf"):
                    found.append((node.lineno, "." * node.level + (node.module or "")))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("mishe_tauftauf"):
                        found.append((node.lineno, alias.name))
    return found


def test_no_package_import_under_dispatch_lock():
    tree = ast.parse(WAKE.read_text(), str(WAKE))
    offenders = []
    for node in ast.walk(tree):
        if _is_lock_with(node):
            offenders += _package_imports(node.body)
    assert offenders == [], (
        "function-level package import under supervisor.exclusive() in wake.py "
        f"(hoist to module level): {offenders}"
    )


def test_delivery_requests_is_bound_at_module_level():
    tree = ast.parse(WAKE.read_text(), str(WAKE))
    top = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 1
        for alias in node.names
    }
    assert "delivery_requests" in top
