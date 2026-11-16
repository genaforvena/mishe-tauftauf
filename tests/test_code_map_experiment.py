import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")
pytest.importorskip("tree_sitter_rust")

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "code_map_experiment.py"


def run_map(root):
    return subprocess.run([sys.executable, str(SCRIPT), str(root)], text=True, capture_output=True)


def test_reports_unused_names_and_incoming_references_across_languages(tmp_path):
    (tmp_path / "a.py").write_text("def used():\n    pass\n\ndef spare():\n    pass\n\nused()\n")
    (tmp_path / "b.rs").write_text("fn live() {}\nfn spare_rust() {}\nfn main() { live(); }\n")
    result = run_map(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "a.py:4 spare" in result.stdout
    assert "b.rs:2 spare_rust" in result.stdout
    assert "a.py:1 used" not in result.stdout
    assert "b.rs:1 live" not in result.stdout
    assert "UNKNOWN" in result.stdout


def test_parse_errors_are_reported_as_unknown(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n")
    result = run_map(tmp_path)
    assert result.returncode != 0
    assert "UNKNOWN" in result.stdout
    assert "bad.py" in result.stdout
