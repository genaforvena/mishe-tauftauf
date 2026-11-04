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
    assert "boundary" in text.lower()
    assert "core" in text.lower()


def test_the_core_does_not_import_the_heavyweight_stack_at_module_scope():
    """The generic core reaches nothing of the plant's machinery at module scope."""
    for path in core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("mishe_tauftauf."), \
                        f"{path.name} imports {alias.name} at module scope"
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("mishe_tauftauf."), \
                    f"{path.name} imports {node.module} at module scope"


def test_importing_the_core_does_not_load_the_tape_or_the_heavy_modules():
    """The generic modules must not drag the plant's heavy modules in."""
    code = (
        "import sys\n"
        "import mishe_tauftauf.core\n"
        "heavy = [name for name in ('mishe_tauftauf.feed', 'mishe_tauftauf.wall',\n"
        "                          'mishe_tauftauf.discovery',\n"
        "                          'mishe_tauftauf.cli')\n"
        "         if name in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", \
        f"importing the core loaded {result.stdout.strip()}"
