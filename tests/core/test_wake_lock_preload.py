"""A wake must not import package modules while it waits on or holds the dispatch lock.

A function-level import inside ``with supervisor.exclusive():`` runs after the
lock is requested, so a release link swapped during the wait can change which
bytes that import loads. Such imports belong at module level, where the
process has them before any lock is taken.

The import is not only lexically inside the ``with`` body: a helper called from
that body may import package modules itself, and those imports run while the
lock is held too. So the check follows the calls the locked body makes into
this module and inspects the functions they reach.
"""
import ast
from pathlib import Path

WAKE = Path(__file__).resolve().parents[2] / "src/mishe_tauftauf/wake.py"


def _is_lock_with(node):
    return isinstance(node, ast.With) and any(
        "exclusive" in ast.unparse(item.context_expr) for item in node.items
    )


def _package_imports(bodies):
    """Package-level imports in the given statement bodies.

    ``bodies`` is a list of statement lists: a ``with`` body, or a function
    body wrapped in a list. Each entry is walked as its own root.
    """
    found = []
    for body in bodies:
        for statement in body:
            for node in ast.walk(statement):
                if isinstance(node, ast.ImportFrom):
                    if node.level > 0 or (node.module or "").startswith("mishe_tauftauf"):
                        found.append((node.lineno, "." * node.level + (node.module or "")))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("mishe_tauftauf"):
                            found.append((node.lineno, alias.name))
    return found


def _module_functions(tree):
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_names(tree, nodes):
    """Names of functions this module defines that the given nodes call."""
    defined = _module_functions(tree)
    called = set()
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name) and target.id in defined:
                    called.add(target.id)
                elif isinstance(target, ast.Attribute) and target.attr in defined:
                    called.add(target.attr)
    return called


def test_no_package_import_under_dispatch_lock():
    tree = ast.parse(WAKE.read_text(), str(WAKE))
    offenders = []
    for node in ast.walk(tree):
        if _is_lock_with(node):
            offenders += _package_imports([node.body])
    assert offenders == [], (
        "function-level package import under supervisor.exclusive() in wake.py "
        f"(hoist to module level): {offenders}"
    )


def test_no_package_import_in_a_function_the_locked_body_calls():
    tree = ast.parse(WAKE.read_text(), str(WAKE))
    functions = _module_functions(tree)
    reachable = set()
    for node in ast.walk(tree):
        if _is_lock_with(node):
            reachable |= _called_names(tree, node.body)
    offenders = []
    for name in sorted(reachable):
        offenders += _package_imports([functions[name].body])
    assert offenders == [], (
        "function-level package import in a function called under the dispatch lock "
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
