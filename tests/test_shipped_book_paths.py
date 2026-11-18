"""The shipped-book path detector must catch a missing file and leave a sound book alone.

The link detector (``checks/shipped-book-links.py``) refuses a Markdown *link*
that leaves the shipped tree.  A procedure can also name a file in prose --
"read ``docs/operating.md``" -- and a reader who cloned the repository has no
such file.  Nothing caught that, because the local checkout still holds the
excluded documents, so a check that reads the working tree sees a healthy book.

Measured on this plant on 2026-10-31 (Docs wake 138278): the shipped
``.agents/skills/plant-mishe/SKILL.md`` -- the file the README's copyable
request tells a reader's agent to use -- instructed the agent to read the
checkout's ``AGENTS.md`` and to "use ``docs/operating.md`` for site discovery
and inspection".  Neither file is in the shipped tree: commit ``237db71f``
("Remove local operating records from the public repository", 2026-10-30) added
the ``.gitignore`` rules that exclude them, and the skill was not updated.

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
CHECK = REPO / "checks" / "shipped-book-paths.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def run_check(tree: Path, repo: Path | None = None) -> subprocess.CompletedProcess:
    command = [sys.executable, str(CHECK), "--home", str(REPO), "--tree", str(tree)]
    if repo is not None:
        command += ["--repo", str(repo)]
    return subprocess.run(command, capture_output=True, text=True)


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    for name, body in pages.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp


def test_the_gate_rejects_a_procedure_that_names_a_file_the_clone_lacks(tmp_path):
    """A directive naming an excluded file must fail, not warn."""
    make_tree(tmp_path, {
        "README.md": "Read `docs/operating.md` before you change anything.\n",
    })
    result = run_check(tmp_path, repo=REPO)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "docs/operating.md" in result.stdout
    assert "FAIL" in result.stdout


def test_the_gate_rejects_a_missing_skill_reference(tmp_path):
    """The measured defect: the shipped skill names a file the clone lacks."""
    make_tree(tmp_path, {
        ".agents/skills/plant-mishe/SKILL.md":
            "Read the checkout's `AGENTS.md`, `README.md`, and the relevant\n"
            "sections of `docs/getting-started.md`.\n",
    })
    result = run_check(tmp_path, repo=REPO)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "AGENTS.md" in result.stdout


def test_the_gate_leaves_a_sound_book_alone(tmp_path):
    """A book whose referenced files are present must pass."""
    make_tree(tmp_path, {
        "README.md": "Read `docs/getting-started.md` before you plant.\n",
        "docs/getting-started.md": "Plant the site and watch one task.\n",
    })
    result = run_check(tmp_path, repo=REPO)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_the_gate_ignores_a_site_relative_path(tmp_path):
    """A site-relative runtime path is correct prose, not a broken reference.

    ``engine/accounts.json`` is never in the repository: it lives in the
    planted site.  Naming it must not be reported, or the check would cry wolf
    on every page that documents a runtime file.
    """
    make_tree(tmp_path, {
        "docs/model-changes.md":
            "`mishe engine accounts` lists the pools recorded in\n"
            "`engine/accounts.json` in the site home.\n",
    })
    result = run_check(tmp_path, repo=REPO)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_ignores_a_path_that_is_not_excluded(tmp_path):
    """Only a path the repository's own ignore rules exclude is a finding.

    A path the repository simply never had is usually a site-relative
    artifact.  The ignore rules are the repository's own statement about
    which paths a clone receives.
    """
    make_tree(tmp_path, {
        "README.md": "See `docs/never-existed.md` for details.\n",
    })
    result = run_check(tmp_path, repo=REPO)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_ignores_a_bare_mention_without_a_directive(tmp_path):
    """Naming a file is not the same as directing the reader to it.

    A sentence that merely mentions an excluded path is not a broken
    procedure; only a directive is.
    """
    make_tree(tmp_path, {
        "README.md": "The plant writes `docs/operating.md` in some setups.\n",
    })
    result = run_check(tmp_path, repo=REPO)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_ignores_historical_records(tmp_path):
    """Field-review records are evidence of past reviews, not reader procedures."""
    make_tree(tmp_path, {
        "research/field-review/reviews/example--critique--2026-10-29.md":
            "The review asked to update `docs/wall-coordination.md`.\n",
    })
    result = run_check(tmp_path, repo=REPO)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_committed_tree_of_this_repository_has_no_missing_reference():
    """This repository's own shipped book must satisfy the check it ships."""
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(REPO)],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_runs_the_shipped_book_path_gate():
    """CI must invoke the detector over the committed bytes.

    ``--repo`` is what makes this a shipped-book gate: it tests the committed
    bytes of HEAD, the bytes a clone receives, rather than the working tree
    (whose gitignored local files would satisfy the reference and hide the
    hole).
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "checks/shipped-book-paths.py" in text, (
        "the shipped-book path detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/shipped-book-paths.py" in line and not line.strip().startswith("#")]
    assert gate_lines, "the path gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the path gate must test the committed bytes (--repo), not the "
        "working tree, whose gitignored files would satisfy the reference")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip() for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/shipped-book-paths.py" in line and not line.startswith("#"))
    pytest_index = next(i for i, line in enumerate(lines)
                         if line.startswith("- run: python -m pytest -q"))
    assert gate_index < pytest_index, (
        "the shipped-book path gate must run before the test suite")
