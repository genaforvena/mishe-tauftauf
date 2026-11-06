"""A wake must not import package modules at call time.

A function-level import runs when the function runs. Wake code runs under the
dispatch lock (``supervisor.dispatch`` holds ``_claim()`` across
``worker.run``), and callables such as ``mail_reader`` are passed in as
parameters, so a call-graph walk from the locked ``with`` body misses them.
A release link swapped during the wait can change which bytes such an import
loads. So the rule is whole-file: no function in wake.py imports a package
module. Package imports belong at module level, where the process has them
before any lock is taken.
"""
import ast
from pathlib import Path

WAKE = Path(__file__).resolve().parents[2] / "src/mishe_tauftauf/wake.py"


def _is_package_import(node):
    if isinstance(node, ast.ImportFrom):
        return node.level > 0 or (node.module or "").startswith("mishe_tauftauf")
    if isinstance(node, ast.Import):
        return any(alias.name.startswith("mishe_tauftauf") for alias in node.names)
    return False


def _function_level_package_imports(source, name="wake.py"):
    """(lineno, function, statement) for each package import inside a function."""
    tree = ast.parse(source, name)
    offenders = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if node is not function and _is_package_import(node):
                offenders.append((node.lineno, function.name, ast.unparse(node)))
    return offenders


def test_no_function_level_package_import_in_wake():
    offenders = _function_level_package_imports(WAKE.read_text(), str(WAKE))
    assert offenders == [], (
        "function-level package import in wake.py (hoist to module level): "
        f"{offenders}"
    )


def test_detector_flags_import_in_a_function_passed_as_a_parameter():
    # Red-first fixture: the offending import is in a callable that the locked
    # body never names by a direct call, so a call-graph check cannot see it.
    fixture = (
        "def default_mail_reader(home, role, after):\n"
        "    from .feed import Feed\n"
        "    return Feed(home)\n"
        "class _Worker:\n"
        "    def run(self, dispatch):\n"
        "        from .delivery_requests import admission_path\n"
        "        return admission_path(dispatch)\n"
    )
    found = _function_level_package_imports(fixture, "fixture.py")
    assert [(name, stmt) for _, name, stmt in found] == [
        ("default_mail_reader", "from .feed import Feed"),
        ("run", "from .delivery_requests import admission_path"),
    ]


def test_delivery_requests_is_bound_at_module_level():
    tree = ast.parse(WAKE.read_text(), str(WAKE))
    top = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 1
        for alias in node.names
    }
    assert "delivery_requests" in top
