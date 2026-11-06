"""A wake must not import package modules at call time.

A function-level import runs when the function runs. Wake code runs under the
dispatch lock (``supervisor.dispatch`` holds ``_claim()`` across
``worker.run``), and callables such as ``mail_reader`` are passed in as
parameters, so a call-graph walk from the locked ``with`` body misses them.
A release link swapped during the wait can change which bytes such an import
loads. So two rules hold:

1. Whole-file, for wake.py: no function in wake.py imports a package module.
2. Reached-code, for the mail reader: every function reachable from
   ``wake.default_mail_reader`` and ``feed.Feed.role_entries`` (resolved through
   package imports and ``self.`` methods, never by bare name) has no
   function-level package import.

Package imports belong at module level, where the process has them before any
lock is taken. Module-level import cycles are broken by leaf modules
(``slugs``, ``feed_errors``, ``wall_text``), which import only the standard
library or each other.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "mishe_tauftauf"
WAKE = SRC / "wake.py"
PKG = "mishe_tauftauf"


def _is_package_import(node):
    if isinstance(node, ast.ImportFrom):
        return node.level > 0 or (node.module or "").startswith(PKG)
    if isinstance(node, ast.Import):
        return any(alias.name.startswith(PKG) for alias in node.names)
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


class _Package:
    """Parsed package modules with top-level defs, class methods and import bindings."""

    def __init__(self, src_root):
        self.trees = {p.stem: ast.parse(p.read_text(), str(p)) for p in Path(src_root).glob("*.py")}
        self.index = {m: self._index(t) for m, t in self.trees.items()}

    @staticmethod
    def _index(tree):
        top, methods, imports = {}, {}, {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top[node.name] = node
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods[(node.name, item.name)] = item
            elif isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                for alias in node.names:
                    imports[alias.asname or alias.name] = (node.module, alias.name)
        return top, methods, imports

    def resolve_name(self, module, name, seen=()):
        """Follow a bare name to the (module, None, name) that defines it, through re-exports."""
        if (module, name) in seen or module not in self.index:
            return None
        top, _, imports = self.index[module]
        if name in top:
            return (module, None, name)
        if name in imports:
            target, original = imports[name]
            return self.resolve_name(target, original, seen + ((module, name),))
        return None

    def node(self, key):
        module, cls, name = key
        top, methods, _ = self.index[module]
        return methods[(cls, name)] if cls else top[name]

    def callees(self, key):
        module, cls, _ = key
        _, methods, _ = self.index[module]
        found = set()
        for call in (n for n in ast.walk(self.node(key)) if isinstance(n, ast.Call)):
            func = call.func
            if isinstance(func, ast.Name):
                resolved = self.resolve_name(module, func.id)
                if resolved:
                    found.add(resolved)
            elif (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                  and func.value.id == "self" and cls and (cls, func.attr) in methods):
                found.add((module, cls, func.attr))
        return found

    def reached(self, roots):
        seen, stack = set(), list(roots)
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            stack.extend(self.callees(key))
        return seen


READER_ROOTS = [
    ("wake", None, "default_mail_reader"),
    ("feed", "Feed", "__init__"),
    ("feed", "Feed", "role_entries"),
]


def reader_offenders(src_root=SRC):
    """Function-level package imports in any function the mail reader reaches."""
    package = _Package(src_root)
    offenders = []
    for key in sorted(package.reached(READER_ROOTS), key=str):
        for node in ast.walk(package.node(key)):
            if node is not package.node(key) and _is_package_import(node):
                offenders.append((key, node.lineno, ast.unparse(node)))
    return offenders


def test_no_function_level_package_import_in_wake():
    offenders = _function_level_package_imports(WAKE.read_text(), str(WAKE))
    assert offenders == [], (
        "function-level package import in wake.py (hoist to module level): "
        f"{offenders}"
    )


def test_no_function_level_package_import_on_the_mail_reader_path():
    offenders = reader_offenders()
    assert offenders == [], (
        "function-level package import reached from the mail reader under the "
        f"dispatch lock (hoist to module level or move to a leaf module): {offenders}"
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
