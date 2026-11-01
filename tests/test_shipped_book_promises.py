"""The shipped-book promise detector must catch an unkept promise and leave a sound book alone.

``checks/shipped-book-links.py`` refuses a Markdown *link* that leaves the
shipped tree, and ``checks/shipped-book-paths.py`` refuses a directive that
names a file the clone lacks.  Both ask whether the reader can *reach* what a
page points at.  Neither asks whether the pointed-at page *contains* it: a
resolved link is not a kept promise.

Measured on this plant on 2026-11-01 (Docs wake 149342): ``docs/mesh.md``
closed with "See ... [Getting started](getting-started.md) for the commands
that write a wall, record an outcome and settle a wake."  The link resolved --
``docs/getting-started.md`` is tracked -- but the page carried one of the
three: ``wall outcome``.  The commands that write a wall (``wall write``) and
that settle a wake (``seed yield``) appeared nowhere in it.  The pointer came
from commit ``31ee26a1``, so the class is not hypothetical: repairing a broken
link by pointing at a tracked page produced a promise the tracked page did
not keep.

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
CHECK = REPO / "checks" / "shipped-book-promises.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def run_check(tree: Path, src: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                           "--tree", str(tree), "--src", str(src)],
                          capture_output=True, text=True)


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    for name, body in pages.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp


def test_the_gate_rejects_a_promise_the_destination_does_not_keep(tmp_path):
    """A page pointing at another for a command it lacks must fail.

    This is the measured defect: the link resolves, and the destination has
    none of the promised commands.
    """
    make_tree(tmp_path, {
        "docs/mesh.md":
            "See [Getting started](getting-started.md) for the commands that\n"
            "write a wall and settle a wake.\n",
        "docs/getting-started.md": "# Plant a site\n\nNothing here yet.\n",
    })
    result = run_check(tmp_path, REPO / "src")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "wall write" in result.stdout
    assert "seed yield" in result.stdout
    assert "FAIL" in result.stdout


def test_the_gate_rejects_a_named_command_the_destination_does_not_keep(tmp_path):
    """A promise that names the command in code is checked the same way."""
    make_tree(tmp_path, {
        "docs/mesh.md":
            "See [Getting started](getting-started.md) for `wall write`.\n",
        "docs/getting-started.md": "# Plant a site\n\nNothing here yet.\n",
    })
    result = run_check(tmp_path, REPO / "src")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "wall write" in result.stdout


def test_the_gate_rejects_a_promise_wrapped_across_lines(tmp_path):
    """A promise whose link and phrase wrap onto the next line is a promise.

    The measured page wrapped it: "See the [README](../README.md) ... and
    [Getting\\nstarted](getting-started.md) for the commands that write a
    wall".  A segment that splits on lines would cut the phrase from its
    link, and the defect would pass again.
    """
    make_tree(tmp_path, {
        "docs/mesh.md":
            "See the [README](../README.md) for an introduction, and [Getting\n"
            "started](getting-started.md) for the commands that write a wall.\n",
        "docs/getting-started.md": "# Plant a site\n\nNothing here yet.\n",
    })
    result = run_check(tmp_path, REPO / "src")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "wall write" in result.stdout


def test_the_gate_leaves_a_kept_promise_alone(tmp_path):
    """A destination that names the promised command passes."""
    make_tree(tmp_path, {
        "docs/mesh.md":
            "See [Getting started](getting-started.md) for the commands that\n"
            "write a wall and settle a wake.\n",
        "docs/getting-started.md":
            "# Plant a site\n\n"
            "```bash\n"
            "\"$SITE/bin/mishe\" --home \"$SITE\" wall write --owner ROLE --file NOTES\n"
            "```\n"
            "```bash\n"
            "\"$SITE/bin/mishe\" --home \"$SITE\" seed yield --slug ROLE --wake N --file NOTES\n"
            "```\n",
    })
    result = run_check(tmp_path, REPO / "src")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_the_gate_rejects_a_table_row_promise(tmp_path):
    """A table row is a promise too.

    "| Write a wall | [Getting started](docs/getting-started.md) |" puts the
    capability *before* its link, in a row that no sentence boundary and no
    blank line separates.  The destination must still carry the command.
    """
    make_tree(tmp_path, {
        "README.md":
            "| You want to… | Read |\n"
            "| --- | --- |\n"
            "| Write a wall | [Getting started](docs/getting-started.md) |\n",
        "docs/getting-started.md": "# Plant a site\n\nNothing here yet.\n",
    })
    result = run_check(tmp_path, REPO / "src")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "wall write" in result.stdout


def test_the_gate_ignores_a_command_that_names_another_clause(tmp_path):
    """A command before the link belongs to the other clause.

    "read the running plant with `mishe view`, or read the [Mesh](mesh.md)
    note" promises nothing of the Mesh page: the command belongs to the
    clause before the link.
    """
    make_tree(tmp_path, {
        "docs/getting-started.md":
            "Next: read the running plant with `mishe view`, or read the\n"
            "[Mesh](mesh.md) culture note.\n",
        "docs/mesh.md": "# Mesh\n\nThe couplings.\n",
    })
    result = run_check(tmp_path, REPO / "src")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_ignores_a_link_to_a_page_the_tree_lacks(tmp_path):
    """An unreachable destination is the link gate's finding, not this one's.

    This detector answers a different question: the destination resolves, and
    does it carry the promised command?  A link that leaves the tree is
    already refused by ``checks/shipped-book-links.py``.
    """
    make_tree(tmp_path, {
        "docs/mesh.md":
            "See [Getting started](getting-started.md) for the commands that\n"
            "write a wall.\n",
    })
    result = run_check(tmp_path, REPO / "src")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_committed_tree_of_this_repository_keeps_its_promises():
    """This repository's own shipped book must satisfy the check it ships."""
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(REPO)],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_runs_the_shipped_book_promise_gate():
    """CI must invoke the detector over the committed bytes.

    ``--repo`` is what makes this a shipped-book gate: it tests the committed
    bytes of HEAD, the bytes a clone receives, rather than the working tree.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "checks/shipped-book-promises.py" in text, (
        "the shipped-book promise detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/shipped-book-promises.py" in line
                  and not line.strip().startswith("#")]
    assert gate_lines, "the promise gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the promise gate must test the committed bytes (--repo), not the "
        "working tree")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip()
             for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/shipped-book-promises.py" in line
                      and not line.startswith("#"))
    pytest_index = next(i for i, line in enumerate(lines)
                         if line.startswith("- run: python -m pytest -q"))
    assert gate_index < pytest_index, (
        "the shipped-book promise gate must run before the test suite")
