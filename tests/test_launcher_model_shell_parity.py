"""The launcher-model parity detector must name a reader the shell contradicts.

``checks/launcher-model-shell-parity.py`` runs the plant's launcher scripts
under ``/bin/sh`` with stand-ins for the pane and compares what the shell
actually passes to the pane with what the reader names. The detector is only
worth its runtime if it is RED when the reader is wrong, so these tests pin
both directions: a good reader over a good home is GREEN, and a reader that
names a model the shell never passes is RED with the candidate named.

The tests are hermetic: they build their own site home, their own copy of the
reader and their own copy of the check, and they never write into the plant's
live home.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "checks" / "launcher-model-shell-parity.py"

GOOD_LAUNCHER = "#!/bin/sh\nexec omp --model=vendor/real --thinking medium\n"
BROKEN_LAUNCHER = "#!/bin/sh\nexec /bin/true\nomp --model=vendor/fake\n"

OLD_READER = '''"""A reader that keeps scanning past an ``exec``, as the plant's did."""
import re


def launcher_model(text):
    match = re.search(r"--model[= ]([^\\s]+)", text)
    return match.group(1) if match else None
'''


def _run(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), *extra],
        capture_output=True, text=True, timeout=300,
    )


def _home(tmp_path: Path, launchers: dict[str, str]) -> Path:
    home = tmp_path / "site"
    minds = home / "minds"
    minds.mkdir(parents=True, exist_ok=True)
    for name, text in launchers.items():
        path = minds / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
    return home


def _tree_with_reader(tmp_path: Path, reader_source: str) -> Path:
    """A copy of the check whose reader is the given source."""
    tree = tmp_path / "tree"
    package = tree / "src" / "mishe_tauftauf"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "omp_pane.py").write_text(reader_source, encoding="utf-8")
    checks = tree / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    (checks / CHECK.name).write_text(CHECK.read_text(encoding="utf-8"), encoding="utf-8")
    return tree


def test_a_good_reader_over_a_good_home_is_green(tmp_path):
    home = _home(tmp_path, {"probe": GOOD_LAUNCHER})
    result = _run(home, "--corpus", "plant")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verdict   : the reader names the model the shell passes" in result.stdout


def test_a_launcher_that_names_a_model_the_shell_never_passes_is_red(tmp_path):
    """The wrong-fact direction: a reader that scans past an ``exec``."""
    tree = _tree_with_reader(tmp_path, OLD_READER)
    home = _home(tmp_path, {"broken": BROKEN_LAUNCHER})
    result = subprocess.run(
        [sys.executable, str(tree / "checks" / CHECK.name), "--home", str(home), "--corpus", "plant"],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "minds/broken" in result.stdout
    assert "reader='vendor/fake' shell=None" in result.stdout


def test_a_reader_that_names_a_model_for_everything_is_red(tmp_path):
    """A check that is always GREEN is not a check; a broken reader must fail it."""
    tree = _tree_with_reader(
        tmp_path,
        "def launcher_model(text):\n    return 'vendor/fake'\n",
    )
    home = _home(tmp_path, {"probe": GOOD_LAUNCHER})
    result = subprocess.run(
        [sys.executable, str(tree / "checks" / CHECK.name), "--home", str(home), "--corpus", "plant"],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "minds/probe" in result.stdout


def test_the_grammar_corpus_is_green_for_the_plant_reader(tmp_path):
    home = _home(tmp_path, {"probe": GOOD_LAUNCHER})
    result = _run(home, "--corpus", "grammar")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_missing_home_is_undecidable(tmp_path):
    result = _run(tmp_path / "absent", "--corpus", "plant")
    assert result.returncode == 3
    assert result.stdout.startswith("UNKNOWN")


def test_a_candidate_that_holds_an_expansion_is_skipped(tmp_path):
    """An expansion can name the plant's own pane, so the check does not run it."""
    home = _home(tmp_path, {"expanded": "#!/bin/sh\nexec $OMP --model=vendor/fake\n"})
    result = _run(home, "--corpus", "plant")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "holds a shell expansion" in result.stdout


def test_the_check_runs_no_program_of_the_candidate(tmp_path):
    """A check must have no effect: the candidate's program never runs."""
    canary = tmp_path / "canary"
    home = _home(tmp_path, {"writer": "#!/bin/sh\nexec /bin/touch %s\n" % canary})
    result = _run(home, "--corpus", "plant")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not canary.exists()
