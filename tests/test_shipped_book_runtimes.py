"""The shipped-book runtime detector must catch an unwired runtime and leave a sound book alone.

``checks/shipped-book-links.py`` asks whether the reader can reach what a
page points at.  ``checks/shipped-book-paths.py`` asks whether the path a
page names exists in the clone.  ``checks/shipped-book-promises.py`` asks
whether the page a promise points at contains what the promise names.

None of them asks whether the thing the page describes *runs*.  A page can
name a real file, in a real module, with a real function -- and the function
can be unreachable: no importer, no CLI leaf, no unit.  The page then reads
as a description of the running plant, and the reader is told about a runtime
that does not exist.

Measured on this plant on 2026-11-01 (Docs wake 150671, from Pub's report in
chat 150242): ``docs/model-changes.md`` carried a section headed "Engine watch
(supervised)" describing a 60-second collector writing
``engine/watch-cache.json``, a stale guard, a Health outbox and a revert
path -- all in the present tense.  The collector is
``src/mishe_tauftauf/engine_watch.py``: no module in ``src/`` imports it, it
registers no CLI leaf, no systemd unit runs it, and the site home holds no
``engine/watch-cache.json``.  Health's disposition (chat 150177) is that the
mechanism is dormant by design, deferred to a named trigger.

These tests exercise the detector against synthetic trees, so a regression in
the detector is caught without depending on the plant's own history staying
broken, and they assert the CI wiring, so removing the step fails the suite
rather than silently re-opening the hole.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-runtimes.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def run_check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                           "--tree", str(tree)],
                          capture_output=True, text=True)


def make_tree(tmp: Path, pages: dict[str, str],
              modules: dict[str, str] | None = None) -> Path:
    """A tree with pages and a plant package.

    The package needs a ``cli.py`` so the detector has an entry point to work
    from; without one, every module would trivially be unreachable and the
    test would prove nothing.
    """
    package = tmp / "src" / "mishe_tauftauf"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        "def main():\n    return 0\n", encoding="utf-8")
    for name, body in (modules or {}).items():
        (package / f"{name}.py").write_text(body, encoding="utf-8")
    for name, body in pages.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp


def test_the_gate_rejects_a_runtime_no_entry_point_reaches(tmp_path):
    """A page presenting an unwired collector as running must fail.

    This is the measured defect: the path, the module and the function are
    all real; nothing starts them.
    """
    make_tree(tmp_path, {
        "docs/model-changes.md":
            "# Changing mind models\n\n"
            "## Engine watch (supervised)\n\n"
            "Collector cycle every 60s reads `engine_usage.status(home)` and\n"
            "writes `engine/watch-cache.json` (0600) with the observed state.\n",
    }, modules={
        "engine_usage": "def status(home):\n    return {}\n",
        "engine_watch":
            "from pathlib import Path\n\n"
            "CACHE_REL = Path(\"engine/watch-cache.json\")\n\n"
            "def once(home):\n    return {}\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "engine/watch-cache.json" in result.stdout
    assert "engine_watch" in result.stdout
    assert "FAIL" in result.stdout


def test_the_gate_accepts_a_dormant_runtime_that_says_so(tmp_path):
    """The same collector, marked as not running, passes.

    A reader must be able to learn that a design exists and is inert.  That
    is the correction this plant needed, and the gate must not punish it.
    """
    make_tree(tmp_path, {
        "docs/model-changes.md":
            "# Changing mind models\n\n"
            "## Engine watch (designed, not running)\n\n"
            "This section describes a mechanism that is not wired and does not\n"
            "run.\n\n"
            "Collector cycle every 60s reads `engine_usage.status(home)` and\n"
            "writes `engine/watch-cache.json` (0600) with the observed state.\n",
    }, modules={
        "engine_usage": "def status(home):\n    return {}\n",
        "engine_watch":
            "from pathlib import Path\n\n"
            "CACHE_REL = Path(\"engine/watch-cache.json\")\n\n"
            "def once(home):\n    return {}\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_the_gate_rejects_a_dormant_marker_in_another_section(tmp_path):
    """A marker binds to its own section, not to the whole page.

    If "dormant" anywhere in the page excused every later claim, a page could
    declare one mechanism dormant and then present a second, running one.
    """
    make_tree(tmp_path, {
        "docs/model-changes.md":
            "# Changing mind models\n\n"
            "## Engine watch (designed, not running)\n\n"
            "This mechanism is dormant.\n\n"
            "## Another watch (supervised)\n\n"
            "The collector writes `engine/watch-cache.json` every 60s.\n",
    }, modules={
        "engine_watch":
            "from pathlib import Path\n\n"
            "CACHE_REL = Path(\"engine/watch-cache.json\")\n\n"
            "def once(home):\n    return {}\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "engine/watch-cache.json" in result.stdout


def test_the_gate_accepts_a_reachable_runtime(tmp_path):
    """A runtime the CLI reaches is a running runtime, and passes."""
    make_tree(tmp_path, {
        "docs/model-changes.md":
            "# Changing mind models\n\n"
            "## Engine watch (supervised)\n\n"
            "The collector writes `engine/watch-cache.json` every 60s.\n",
    }, modules={
        "engine_watch":
            "from pathlib import Path\n\n"
            "CACHE_REL = Path(\"engine/watch-cache.json\")\n\n"
            "def once(home):\n    return {}\n",
        "cli": "from . import engine_watch\n\n\ndef main():\n    return 0\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_a_test_naming_a_module_does_not_make_it_an_entry_point(tmp_path):
    """A test is not a scheduler.

    ``tests/test_engine_watch.py`` names the module it exercises.  Reading
    that as a start would make every tested module look scheduled, which is
    exactly the confusion this check exists to resolve.
    """
    make_tree(tmp_path, {
        "docs/model-changes.md":
            "# Changing mind models\n\n"
            "The collector writes `engine/watch-cache.json` every 60s.\n",
    }, modules={
        "engine_watch":
            "from pathlib import Path\n\n"
            "CACHE_REL = Path(\"engine/watch-cache.json\")\n\n"
            "def once(home):\n    return {}\n",
    })
    tests = tmp_path / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_engine_watch.py").write_text(
        "import mishe_tauftauf.engine_watch as ew\n", encoding="utf-8")
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "engine_watch" in result.stdout


def test_the_gate_ignores_a_path_nothing_in_the_tree_writes(tmp_path):
    """A file the operator creates by hand is not a runtime claim.

    The plant writes some files that no module owns -- an operator note, a
    hand-made evidence file.  A page may name those.
    """
    make_tree(tmp_path, {
        "docs/model-changes.md":
            "# Changing mind models\n\n"
            "The key goes in `engine/go-b-store` by hand.\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_ignores_a_path_inside_a_fenced_example(tmp_path):
    """A fenced block is an illustration, not a claim that the plant writes it."""
    make_tree(tmp_path, {
        "docs/model-changes.md":
            "# Changing mind models\n\n"
            "```sh\n"
            "cat engine/watch-cache.json\n"
            "```\n",
    }, modules={
        "engine_watch":
            "from pathlib import Path\n\n"
            "CACHE_REL = Path(\"engine/watch-cache.json\")\n\n"
            "def once(home):\n    return {}\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_committed_tree_of_this_repository_runs_what_it_documents():
    """This repository's own shipped book must satisfy the check it ships."""
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(REPO)],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_runs_the_shipped_book_runtime_gate():
    """CI must invoke the detector over the committed bytes.

    ``--repo`` is what makes this a shipped-book gate: it tests the committed
    bytes of HEAD, the bytes a clone receives, rather than the working tree.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "checks/shipped-book-runtimes.py" in text, (
        "the shipped-book runtime detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/shipped-book-runtimes.py" in line
                  and not line.strip().startswith("#")]
    assert gate_lines, "the runtime gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the runtime gate must test the committed bytes (--repo), not the "
        "working tree")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip()
             for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/shipped-book-runtimes.py" in line
                      and not line.startswith("#"))
    suite_index = next(i for i, line in enumerate(lines)
                       if line.startswith("- run: python -m pytest"))
    assert gate_index < suite_index, (
        "the shipped-book runtime gate must run before the test suite")
