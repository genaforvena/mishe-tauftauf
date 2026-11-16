import os
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


def test_rooted_reference_experiment_keeps_unknowns_and_follows_transitive_names(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def entry():\n    middle()\n\ndef middle():\n    leaf()\n\ndef leaf():\n    pass\n\ndef orphan():\n    pass\n")
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", f"{source}:1", str(source)], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sample.py:4 middle" in result.stdout
    assert "sample.py:7 leaf" in result.stdout
    assert f"UNKNOWN {source}:10 orphan" in result.stdout
    for limitation in ("dynamic dispatch", "macros", "incomplete roots", "unscanned consumers"):
        assert limitation in result.stdout


def test_rooted_reference_experiment_rejects_missing_root_and_parse_error(tmp_path):
    source = tmp_path / "broken.rs"
    source.write_text("fn main( {\n")
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", f"{source}:99", str(source)], text=True, capture_output=True)
    assert result.returncode != 0
    assert "UNKNOWN" in result.stdout
    assert "root" in result.stdout
    assert "parse" in result.stdout


def test_rust_test_and_dynamic_python_use_remain_unknown(tmp_path):
    rust = tmp_path / "main.rs"
    rust.write_text("fn main() { live(); }\nfn live() {}\n#[test]\nfn registered_test() {}\n")
    python = tmp_path / "a.py"
    python.write_text("def entry():\n    globals()['hidden']()\n\ndef hidden():\n    pass\n")
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", f"{rust}:1", "--root", f"{python}:1", str(tmp_path)], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"POSSIBLE {rust}:2 live" in result.stdout
    assert f"UNKNOWN {rust}:4 registered_test" in result.stdout
    assert f"UNKNOWN {python}:4 hidden" in result.stdout


def test_same_name_in_an_unrelated_module_is_only_a_possible_path(tmp_path):
    entry = tmp_path / "entry.py"
    entry.write_text("def main():\n    other.walk()\n")
    unrelated = tmp_path / "unrelated.py"
    unrelated.write_text("def walk():\n    pass\n")
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", f"{entry}:1", str(tmp_path)], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"POSSIBLE {unrelated}:1 walk" in result.stdout
    assert "not proven calls" in result.stdout


def test_unreadable_nested_directory_is_incomplete(tmp_path):
    (tmp_path / "visible.py").write_text("def visible():\n    pass\n")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "hidden.py").write_text("def hidden():\n    pass\n")
    hidden.chmod(0)
    try:
        if os.access(hidden, os.R_OK):
            pytest.skip("process can read permissionless directory")
        result = run_map(tmp_path)
    finally:
        hidden.chmod(0o700)
    assert result.returncode != 0, result.stdout
    assert "UNKNOWN" in result.stdout
    assert str(hidden) in result.stdout
