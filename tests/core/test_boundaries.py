"""The core's boundary contract: it stays small and it stays separate."""
import ast
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "mishe_tauftauf" / "core"


def core_modules():
    return sorted(path for path in CORE.glob("*.py"))


def test_the_core_has_a_readme_that_names_its_boundary():
    readme = CORE / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    assert "adapter" in text.lower()
    assert "core" in text.lower()


def test_the_core_does_not_import_the_heavyweight_stack_at_module_scope():
    """Only the adapters may reach the plant's machinery, and only inside a function."""
    allowed = {"adapters.py"}
    for path in core_modules():
        if path.name in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("mishe_tauftauf."), \
                        f"{path.name} imports {alias.name} at module scope"
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("mishe_tauftauf."), \
                    f"{path.name} imports {node.module} at module scope"


def test_importing_the_core_does_not_load_the_tape_or_the_tmux_layer():
    """The generic modules must not drag the plant's heavy modules in."""
    code = (
        "import sys\n"
        "import mishe_tauftauf.core\n"
        "heavy = [name for name in ('mishe_tauftauf.feed', 'mishe_tauftauf.wall',\n"
        "                          'mishe_tauftauf.tmux', 'mishe_tauftauf.seed',\n"
        "                          'mishe_tauftauf.inference_effects')\n"
        "         if name in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", \
        f"importing the core loaded {result.stdout.strip()}"


def test_the_adapters_reach_the_stack_only_inside_a_function():
    path = CORE / "adapters.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_level_parents = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_level_parents.append(node)
    for node in module_level_parents:
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            names = [node.module or ""]
        for name in names:
            assert not name.startswith("mishe_tauftauf."), \
                f"adapters.py imports {name} at module scope"


def test_the_prototype_supervisor_is_reachable_from_the_core():
    """The prototype stays importable; the core is where new work lives."""
    result = subprocess.run(
        [sys.executable, "-c",
         "from mishe_tauftauf.minimal_supervisor import MinimalSupervisor;"
         "print(MinimalSupervisor.__name__)"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "MinimalSupervisor"
