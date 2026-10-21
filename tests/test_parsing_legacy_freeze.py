"""The frozen legacy parsing budget must not be widened.

`tools/check_parsing.py` lets a file's parsing sites through when the policy's
`legacy` section carries a matching expression fingerprint.  That budget is
meant to be frozen debt: AGENTS.md says it "must not be widened to pass a
change" and `docs/protocol-boundaries.md` says "Do not regenerate the legacy
budget to make a failing change pass".  Neither sentence was enforced, so a
budget entry added beside a failing change made the guard pass again, silently.

Measured on this plant on 2026-10-21 (discover wake 81754): the budget was
widened once, by commit ``23f9b1e6`` on 2026-10-10, which raised one
fingerprint's count from 6 to 7 and added another, in exchange for the
"drift-across-sites skips units with description naming checkout root" fix.
The widening is still in the policy, one site of it
(``src/mishe_tauftauf/discovery.py``, fingerprint ``ae27c7bb...``) is above the
budget the policy carried when it was frozen on 2026-10-07, and every check in
the guard suite passed throughout.

These tests exercise the detector against synthetic repositories, so a
regression in the detector is caught without depending on the plant's own
policy staying as it is.

The budget is a *count* per expression, and the tree does not always hold
that many sites.  A free slot is a hole: a new parsing site that reuses an
expression the budget already allows changes no policy, passes the guard, and
grows the debt.  Measured on this plant on 2026-10-21 (discover wake 83659):
16 such slots, 6 of them reachable by copying an expression that exists
elsewhere in the tree.  The freeze therefore also records what the tree really
held, and a site the freeze does not know is new debt.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "parsing-legacy-freeze.py"
FP_A = "a" * 64
FP_B = "b" * 64
CORE_SOURCE = "value = raw.split(':')\n"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin"}


def fingerprint_of(source: str) -> str:
    """The guard's own fingerprint for the first parsing site in `source`."""
    spec = importlib.util.spec_from_file_location("check_parsing_test",
                                                  REPO / "tools" / "check_parsing.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.inspect_source("src/core.py", source)[0].fingerprint


FP_CORE = fingerprint_of(CORE_SOURCE)


def run(repo: Path, *extra: str, check: Path = CHECK) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(check), "--home", str(REPO),
                           "--repo", str(repo), *extra], capture_output=True, text=True)


def run_guard(repo: Path) -> subprocess.CompletedProcess:
    """The plant's own guard, on the fixture's bytes."""
    return subprocess.run([sys.executable, "tools/check_parsing.py"], cwd=repo,
                          capture_output=True, text=True)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, env={**GIT_ENV, "HOME": str(repo)})


def make_repo(tmp_path: Path, legacy: dict, check: Path = CHECK) -> Path:
    """A repository whose policy carries `legacy` and whose freeze matches it."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tools").mkdir()
    shutil.copy(REPO / "tools" / "check_parsing.py", repo / "tools" / "check_parsing.py")
    (repo / "src" / "adapter.py").write_text("", encoding="utf-8")
    (repo / "src" / "core.py").write_text(CORE_SOURCE, encoding="utf-8")
    (repo / "parsing-policy.json").write_text(
        json.dumps({"version": 1, "boundaries": ["src/adapter.py", "tools/check_parsing.py"],
                    "legacy": legacy}, indent=2),
        encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "policy")
    created = run(repo, "--update-baseline", check=check)
    assert created.returncode == 0, created.stdout + created.stderr
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "freeze")
    return repo


def write_legacy(repo: Path, legacy: dict) -> None:
    path = repo / "parsing-policy.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["legacy"] = legacy
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_a_budget_equal_to_the_freeze_passes(tmp_path):
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    result = run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_a_new_budgeted_path_fails(tmp_path):
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    write_legacy(repo, {"src/core.py": {FP_A: 1}, "src/other.py": {FP_B: 1}})
    result = run(repo, "--working-tree")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "src/other.py" in result.stdout
    assert "does not know this path" in result.stdout


def test_more_sites_in_a_known_path_fails(tmp_path):
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    write_legacy(repo, {"src/core.py": {FP_A: 2}})
    result = run(repo, "--working-tree")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "budgeted sites grew from 1 to 2" in result.stdout


def test_a_rewritten_budget_at_the_same_size_fails(tmp_path):
    """Same number of sites, different expressions: the budget was regenerated."""
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    write_legacy(repo, {"src/core.py": {FP_B: 1}})
    result = run(repo, "--working-tree")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "different set of expressions" in result.stdout


def test_a_smaller_budget_passes_and_names_the_standing_allowance(tmp_path):
    """A migration shrinks the debt; that is allowed, and the allowance is visible."""
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}, "src/other.py": {FP_B: 1}})
    write_legacy(repo, {"src/core.py": {FP_A: 1}})
    result = run(repo, "--working-tree")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "standing allowance" in result.stdout
    assert "src/other.py" in result.stdout


def test_a_pending_widening_is_visible_before_it_is_committed(tmp_path):
    """The forward direction: the edit is on disk, the committed tree is clean."""
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    write_legacy(repo, {"src/core.py": {FP_A: 1}, "src/other.py": {FP_B: 1}})
    pending = run(repo, "--working-tree")
    assert pending.returncode == 2, pending.stdout + pending.stderr
    committed = run(repo)
    assert committed.returncode == 0, committed.stdout + committed.stderr


def test_a_missing_freeze_is_unknown_and_never_ok(tmp_path):
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    (repo / "parsing-legacy-freeze.json").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "drop the freeze")
    result = run(repo)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stderr


def test_a_broken_policy_is_unknown_and_never_ok(tmp_path):
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    (repo / "parsing-policy.json").write_text("broken", encoding="utf-8")
    result = run(repo, "--working-tree")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stderr


def test_a_broken_freeze_is_unknown_and_never_ok(tmp_path):
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    (repo / "parsing-legacy-freeze.json").write_text("{", encoding="utf-8")
    result = run(repo, "--working-tree")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stderr


def test_a_baseline_update_refuses_to_widen(tmp_path):
    """The freeze is a record of what the plant owes; it may not grow on demand."""
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    before = (repo / "parsing-legacy-freeze.json").read_text(encoding="utf-8")
    write_legacy(repo, {"src/core.py": {FP_A: 1}, "src/other.py": {FP_B: 1}})
    result = run(repo, "--update-baseline", "--working-tree")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSED" in result.stdout
    assert (repo / "parsing-legacy-freeze.json").read_text(encoding="utf-8") == before


def test_a_baseline_update_shrinks_with_a_migration(tmp_path):
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}, "src/other.py": {FP_B: 1}})
    write_legacy(repo, {"src/core.py": {FP_A: 1}})
    result = run(repo, "--update-baseline", "--working-tree")
    assert result.returncode == 0, result.stdout + result.stderr
    frozen = json.loads((repo / "parsing-legacy-freeze.json").read_text(encoding="utf-8"))
    assert set(frozen["paths"]) == {"src/core.py"}


def test_the_repository_budget_is_not_wider_than_the_freeze():
    """The plant's own committed bytes must not carry debt the freeze forbids."""
    result = run(REPO)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_a_new_site_inside_a_free_budget_slot_is_new_debt(tmp_path):
    """The hole: a free slot takes a new site, the guard passes, the debt grows.

    The policy allows two sites of one expression and the tree holds one.  A
    second site changes no policy byte; the guard consumes the free slot and
    passes; the freeze must still see the debt grow.
    """
    repo = make_repo(tmp_path, {"src/core.py": {FP_CORE: 2}})
    assert run(repo).returncode == 0
    (repo / "src" / "core.py").write_text(CORE_SOURCE + "second = raw.split(':')\n", encoding="utf-8")
    guard = run_guard(repo)
    assert guard.returncode == 0, guard.stdout + guard.stderr
    result = run(repo, "--working-tree")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "the tree holds 2 site(s)" in result.stdout
    assert "without a policy edit" in result.stdout


def test_a_migration_that_drops_a_site_passes_and_names_the_allowance(tmp_path):
    """A tree that holds fewer sites than the freeze records is a migration."""
    repo = make_repo(tmp_path, {"src/core.py": {FP_CORE: 1}})
    (repo / "src" / "core.py").write_text("value = 1\n", encoding="utf-8")
    result = run(repo, "--working-tree")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "the tree dropped" in result.stdout


def test_a_freeze_without_observed_counts_is_unknown_and_never_ok(tmp_path):
    """A record that cannot say what the tree held cannot say the debt is frozen."""
    repo = make_repo(tmp_path, {"src/core.py": {FP_A: 1}})
    path = repo / "parsing-legacy-freeze.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = 1
    for entry in data["paths"].values():
        entry.pop("observed", None)
        entry.pop("observed_sites", None)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    result = run(repo, "--working-tree")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stderr


def test_a_baseline_update_refuses_to_grow_the_observed_debt(tmp_path):
    """The freeze may not be re-based to accept a new site that appeared."""
    repo = make_repo(tmp_path, {"src/core.py": {FP_CORE: 2}})
    before = (repo / "parsing-legacy-freeze.json").read_text(encoding="utf-8")
    (repo / "src" / "core.py").write_text(CORE_SOURCE + "second = raw.split(':')\n", encoding="utf-8")
    result = run(repo, "--update-baseline", "--working-tree")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSED" in result.stdout
    assert (repo / "parsing-legacy-freeze.json").read_text(encoding="utf-8") == before


def _mutant(tmp_path: Path, name: str, old: str, new: str) -> Path:
    """A copy of the detector with one decision changed."""
    source = CHECK.read_text(encoding="utf-8")
    assert old in source, f"the mutant {name} does not match the detector"
    path = tmp_path / f"mutant-{name}.py"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return path


MUTANTS = (
    ("new-path", 'if path not in freeze:', 'if False:'),
    ("grow", 'if got["sites"] > allowed["sites"]:', 'if False:'),
    ("rewrite", 'and got["digest"] != allowed["digest"]', 'and False'),
    ("observed", 'if count > was:', 'if False:'),
    ("verdict", '        return 2\n    total = sum', '        return 0\n    total = sum'),
)


def test_each_mutant_is_caught(tmp_path):
    """Every decision the detector makes is load-bearing.

    A detector that cannot fail is worse than no detector: the plant would
    read a PASS as an observation.
    """
    scenarios = {
        "new-path": ({"src/core.py": {FP_A: 1}}, {"src/core.py": {FP_A: 1}, "src/other.py": {FP_B: 1}}, None),
        "grow": ({"src/core.py": {FP_A: 1}}, {"src/core.py": {FP_A: 2}}, None),
        "rewrite": ({"src/core.py": {FP_A: 1}}, {"src/core.py": {FP_B: 1}}, None),
        "observed": ({"src/core.py": {FP_CORE: 2}}, None,
                     CORE_SOURCE + "second = raw.split(':')\n"),
        "verdict": ({"src/core.py": {FP_A: 1}}, {"src/core.py": {FP_A: 1}, "src/other.py": {FP_B: 1}}, None),
    }
    for name, old, new in MUTANTS:
        mutant = _mutant(tmp_path, name, old, new)
        legacy, widened, tree = scenarios[name]
        repo = make_repo(tmp_path / name, legacy, check=mutant)
        if widened is not None:
            write_legacy(repo, widened)
        if tree is not None:
            (repo / "src" / "core.py").write_text(tree, encoding="utf-8")
        result = run(repo, "--working-tree", check=mutant)
        assert result.returncode != 2, (
            f"mutant {name} still reports the widening as a failure: "
            f"{result.stdout}{result.stderr}")
