"""The shipped-book evidence detector must catch a stale rule and leave a sound book alone.

The detector reads a shipped page's stated rule about resolving a relative
outcome-evidence path, and compares it with what the **committed tree** does
when the real ``wall outcome`` command runs with that relative path from a
working directory outside the site.

What these tests pin:

* a page that says the relative form is refused fails when the tree accepts
  it (the defect measured on 2026-11-01, Docs wake 151606);
* a page that says the relative form is accepted passes when the tree
  accepts it;
* the rule is read per *rule*, so a bullet list that states several evidence
  rules is not judged by an unrelated rule's outcome;
* the tree is exercised, so a page that states nothing cannot pass by
  accident, and a tree that cannot be exercised reads UNKNOWN rather than a
  pass;
* this repository's own shipped book satisfies the check it ships;
* CI runs the gate over the committed bytes, before the test suite.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-evidence.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

STALE_PAGE = """# Evidence

- A relative path is resolved against the current working directory first.
  From the repository root, `artifacts/NAME.md` means the repository's own
  `artifacts/` directory, which is not the site's and is refused.
"""

CURRENT_PAGE = """# Evidence

- A relative path is resolved against the current working directory first.
  When that form does not name an eligible file in the site, the same relative
  path is resolved against the site home instead, so `artifacts/NAME.md` from
  the repository root names the **site's** `artifacts/NAME.md` and is accepted.
"""

# A page that states the rule and, in the same bullet list, another rule
# whose outcome differs.  The detector must judge the resolution rule, not
# the other rule's outcome.
MIXED_PAGE = """# Evidence

- A file in a subdirectory, such as `artifacts/wake-1234/evidence.md`, is
  refused. The rule counts directory levels, so only files sitting directly in
  the named directory qualify.
- A relative path is resolved against the current working directory first.
  When that form does not name an eligible file in the site, the same relative
  path is resolved against the site home instead, so `artifacts/NAME.md` from
  the repository root names the **site's** `artifacts/NAME.md` and is accepted.
"""


def run_check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(REPO / ".mishe-tauftauf"),
                           "--tree", str(tree)],
                          capture_output=True, text=True)


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    """A tree with pages and the plant package the check exercises.

    The check *runs* the tree's own code -- it plants a site and executes
    ``wall outcome`` -- so the tree needs a package that actually runs.  The
    repository's own ``src/`` is copied in, so the tree behaves like a clone
    of this repository with different pages.
    """
    package = tmp / "src" / "mishe_tauftauf"
    package.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "src" / "mishe_tauftauf", package,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name, body in pages.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp


def test_the_gate_rejects_a_page_that_states_a_stale_rule(tmp_path):
    """A page claiming the relative form is refused must fail when it is accepted."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": STALE_PAGE})
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "STALE EVIDENCE RULE" in result.stdout
    assert "docs/getting-started.md" in result.stdout


def test_the_gate_accepts_a_page_that_states_the_current_rule(tmp_path):
    """The same page, corrected, passes."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_the_gate_judges_the_resolution_rule_not_another_rule(tmp_path):
    """A bullet list states several rules; each is judged on its own outcome."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": MIXED_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_observes_the_tree_rather_than_asserting_a_result(tmp_path):
    """The observed outcome is read from the tree and reported."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    result = run_check(tree)
    assert "observed outside=accepted" in result.stdout, result.stdout


def test_a_page_that_states_nothing_is_not_a_finding(tmp_path):
    """A page about something else is not evidence about this rule."""
    tree = make_tree(tmp_path, {"docs/other.md": "# Other\n\nThe plant is local.\n"})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "statements=0" in result.stdout, result.stdout


def test_a_tree_that_cannot_be_exercised_reads_unknown(tmp_path):
    """A tree the command cannot run in reads UNKNOWN, never a pass."""
    tree = tmp_path / "empty"
    tree.mkdir()
    result = run_check(tree)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_the_single_use_refusal_is_not_the_rules_outcome(tmp_path):
    """A re-bound artifact is a different refusal, and must not read as the rule.

    "outcome evidence path already bound" is the single-use evidence
    contract, not the rule about where a relative path resolves.  Reading it
    as the rule's outcome would turn a re-bound artifact into a rule
    difference and report a page that states the rule correctly as stale.
    """
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already bound" not in result.stdout, result.stdout
    assert "observed outside=accepted" in result.stdout, result.stdout


def test_the_committed_tree_of_this_repository_runs_what_it_documents():
    """This repository's own shipped book must satisfy the check it ships."""
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO / ".mishe-tauftauf"),
                              "--repo", str(REPO)],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_runs_the_shipped_book_evidence_gate():
    """CI must invoke the detector over the committed bytes."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "checks/shipped-book-evidence.py" in text, (
        "the shipped-book evidence detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/shipped-book-evidence.py" in line
                  and not line.strip().startswith("#")]
    assert gate_lines, "the evidence gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the evidence gate must test the committed bytes (--repo), not the "
        "working tree")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip()
             for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/shipped-book-evidence.py" in line
                      and not line.startswith("#"))
    suite_index = next(i for i, line in enumerate(lines)
                       if line.startswith("- run: python -m pytest"))
    assert gate_index < suite_index, (
        "the shipped-book evidence gate must run before the test suite")
