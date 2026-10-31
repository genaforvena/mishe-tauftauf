"""The shipped-book link detector must catch a dangling link and leave a sound book alone.

The public reader receives exactly the committed tree.  A relative link in a
tracked page that points at a path the tree does not contain is broken for
every reader who clones the repository, and the failure is silent: the local
checkout still holds the excluded files, so a link check that reads the
working tree sees a healthy book.

Measured on this plant on 2026-10-31 (Docs wake 75277): a fresh clone of
``d951c928`` had **20 broken relative links** across three tracked files --
``README.md``, ``docs/getting-started.md`` and ``docs/completions-judge.md``.
Commit ``237db71f`` ("Remove local operating records from the public
repository", 2026-10-30) added the public guide ``docs/getting-started.md``
and, in the same commit, added the ``.gitignore`` rules that excluded
``docs/operating.md``, ``docs/how-it-works.md``, ``docs/wall-coordination.md``,
``docs/agent-step-experiments.md``, ``docs/decision-models.md`` and
``AGENTS.md``.

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
CHECK = REPO / "checks" / "shipped-book-links.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def run_check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                           "--tree", str(tree)],
                          capture_output=True, text=True)


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    for name, body in pages.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp


def test_the_gate_rejects_a_tree_with_a_dangling_relative_link(tmp_path):
    """A link to a file the tree does not contain must fail, not warn."""
    make_tree(tmp_path, {
        "README.md": "See the [guide](docs/guide.md).\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "docs/guide.md" in result.stdout
    assert "FAIL" in result.stdout


def test_the_gate_rejects_a_dangling_anchor_in_a_file_that_exists(tmp_path):
    """A present file with a missing anchor is still a link a reader cannot follow."""
    make_tree(tmp_path, {
        "README.md": "See the [section](docs/guide.md#missing-section).\n",
        "docs/guide.md": "# A real heading\n\nBody.\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BROKEN ANCHOR" in result.stdout


def test_the_gate_accepts_a_sound_book(tmp_path):
    """A sound book must pass, or the gate cannot be a gate."""
    make_tree(tmp_path, {
        "README.md": "See the [guide](docs/guide.md) and its "
                     "[section](docs/guide.md#a-real-heading).\n",
        "docs/guide.md": "# A real heading\n\nBody.\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_the_gate_ignores_external_and_bare_anchors(tmp_path):
    """External URLs and in-page anchors are out of scope for this check."""
    make_tree(tmp_path, {
        "README.md": ("See [upstream](https://example.invalid/page) and "
                      "[below](#a-real-heading).\n\n# A real heading\n"),
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_ignores_links_inside_fenced_code(tmp_path):
    """A Markdown link shown as an example is prose, not a broken link."""
    make_tree(tmp_path, {
        "README.md": ("Example:\n\n```markdown\n[not shipped](docs/missing.md)\n```\n"),
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_ignores_links_inside_inline_code(tmp_path):
    """A link shown as inline code is an example, not a link the reader follows."""
    make_tree(tmp_path, {
        "README.md": "Example: `[not shipped](docs/missing.md)`\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_rejects_a_link_that_leaves_the_shipped_tree(tmp_path):
    """A link out of the tree is broken even when the file exists next to it.

    The reader receives the shipped tree and nothing else.  A link to
    ``../outside.md`` resolves on this host because the checkout's parent
    directory happens to hold that file, and a clone has no such file -- so
    the check must judge the link against the tree, not against the host.
    """
    tree = make_tree(tmp_path, {
        "README.md": "See [outside](../outside.md).\n",
    })
    (tmp_path / "outside.md").write_text("# Outside the tree\n", encoding="utf-8")
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "leaves the shipped tree" in result.stdout


def test_the_gate_rejects_a_reference_link_with_no_resolvable_target(tmp_path):
    """A reference-style link whose target is absent is a link the reader cannot follow."""
    make_tree(tmp_path, {
        "README.md": "See the [guide][g].\n\n[g]: docs/missing.md\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "docs/missing.md" in result.stdout


def test_the_gate_accepts_a_reference_link_that_resolves(tmp_path):
    """A reference-style link to a shipped file must pass, or the gate is noise."""
    make_tree(tmp_path, {
        "README.md": "See the [guide][g].\n\n[g]: docs/guide.md\n",
        "docs/guide.md": "# A real heading\n\nBody.\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_does_not_call_a_python_annotation_a_broken_link(tmp_path):
    """A bracketed use with no definition is literal text, not a link.

    CommonMark renders ``[str]`` as the text ``[str]`` when no definition
    exists, so it is not a link a reader cannot follow.  Prose in this
    repository carries such annotations, and calling them broken would make
    the gate cry wolf over every page that names a type.
    """
    make_tree(tmp_path, {
        "README.md": "The value is `Final[str]` and the map is "
                     "`dict[str, dict[str, object]]`.\n",
    })
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_reads_committed_bytes_not_the_working_tree(tmp_path):
    """An untracked working-tree file must not satisfy a link.

    This is the whole point of the check: the local checkout holds the
    gitignored operating documents that the public repository excludes, so a
    check that reads the working tree cannot see the defect.
    """
    tree = make_tree(tmp_path, {
        "README.md": "See the [guide](docs/guide.md).\n",
    })
    (tree / "docs").mkdir(exist_ok=True)
    (tree / "docs" / "guide.md").write_text("# Present only in the tree\n",
                                            encoding="utf-8")
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_committed_tree_of_this_repository_has_no_dangling_link():
    """This repository's own shipped book must satisfy the check it ships."""
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(REPO)],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_runs_the_shipped_book_link_gate():
    """CI must invoke the detector over the committed bytes.

    ``--repo`` is what makes this a shipped-book gate: it tests the committed
    bytes of HEAD, the bytes a clone receives, rather than the working tree
    (whose gitignored local files would satisfy the links and hide the hole).
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "checks/shipped-book-links.py" in text, (
        "the shipped-book link detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/shipped-book-links.py" in line and not line.strip().startswith("#")]
    assert gate_lines, "the link gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the link gate must test the committed bytes (--repo), not the "
        "working tree, whose gitignored files would satisfy the links")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip() for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/shipped-book-links.py" in line and not line.startswith("#"))
    pytest_index = next(i for i, line in enumerate(lines)
                         if line.startswith("- run: python -m pytest -q"))
    assert gate_index < pytest_index, (
        "the link gate must run before the suite so a broken shipped book "
        "fails the build itself")
