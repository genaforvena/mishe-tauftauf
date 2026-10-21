"""The observer bundle refresh must carry the closure, verify, and be reversible.

The bundle the plant's renderer scripts import from is a copy of the reviewed
tree, and it went stale in two ways on 2026-10-20: a module that imports
cleanly but publishes old facts, and a module whose sibling predates the name
the reviewed projection imports. Refreshing one file at a time therefore
produced a bundle that either broke the evidence leg or published a stale
reading, and the plant published the result as healthy.

``tools/refresh-observer-bundle.py`` makes the order of a refresh a decision
the tool makes, by running the refresh on a disposable copy and reading the
failure, and by refusing to write to the plant's bundle until the refreshed
copy has been seen to run and to publish the reviewed tree's facts.

These tests use a synthetic package, so the plant's own bundle is never
touched: the tool is pointed at a temporary home whose renderer script names
a temporary bundle.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "refresh-observer-bundle.py"
PACKAGE = "mishe_tauftauf"
SLUG = "research-methods"

REFUSAL_LINE = "OUTCOME WRITER REFUSALS: 3 in the last 24h (0 coverage denials)"
REVIEWED_ROW = "role: OBSERVED; wakes=1 checked-evidence-age=none"
STALE_ROW = "role: OBSERVED; wakes=1"


def write_package(root: Path, *, carries_name: bool, row: str, projection_line: bool) -> Path:
    """A package whose evidence leg works, or fails on a missing sibling name.

    ``carries_name`` selects whether the package's copy of ``wall.py`` carries
    the name the reviewed projection imports. The bundle's copy does not, so
    refreshing the projection alone breaks the leg.
    ``row`` is the role row the package's ``initiative.py`` publishes, which is
    where a stale-but-importable module carries a stale fact.
    ``projection_line`` selects whether the refusal projection is published.
    """
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    wall = ["REFUSAL_AUDIT = 'logs/outcome-refusal-audit.jsonl'"]
    if carries_name:
        wall.append("OUTCOME_REFUSAL_AUDIT = REFUSAL_AUDIT")
    (package / "wall.py").write_text("\n".join(wall) + "\n")
    (package / "initiative.py").write_text("ROWS = [%r]\n" % (row,))
    body = []
    if carries_name:
        body.append("from .wall import OUTCOME_REFUSAL_AUDIT")
    else:
        body.append("from .wall import REFUSAL_AUDIT")
    body.append("from .initiative import ROWS")
    body.append("")
    body.append("LINES = []")
    if projection_line:
        body.append("LINES.append(%r)" % (REFUSAL_LINE,))
    body.append("LINES.extend(ROWS)")
    body.append("")
    body.append("def lines(home):")
    body.append("    return list(LINES)")
    (package / "follow_through.py").write_text("\n".join(body) + "\n")
    return root


def write_renderer(home: Path, bundle: Path) -> Path:
    """A top-pain script naming the bundle, in the plant's own shape."""
    directory = home / "top-pains"
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / SLUG
    script.write_text(
        "#!/bin/sh\n"
        "true\n"
        "status=$?\n"
        "PYTHONPATH='%s' python3 -c \"import sys;"
        "sys.path.insert(0, '%s');"
        "from %s import follow_through;"
        "print(chr(10).join(follow_through.lines(None)))\"\n"
        "exit \"$status\"\n"
        % (bundle, bundle, PACKAGE)
    )
    script.chmod(0o755)
    return script


def digest_tree(root: Path) -> dict:
    """The sha256 of every module in a package, by name."""
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / PACKAGE).glob("*.py"))}


@pytest.fixture
def plant(tmp_path):
    """A site home whose renderer script names a bundle the tool may refresh."""
    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    bundle = tmp_path / "observer" / "src"
    write_package(bundle, carries_name=False, row=STALE_ROW, projection_line=False)
    write_renderer(home, bundle)
    return home, bundle


@pytest.fixture
def reviewed(tmp_path):
    """The reviewed tree: the projection, the name and the current row."""
    return write_package(tmp_path / "reviewed", carries_name=True, row=REVIEWED_ROW,
                          projection_line=True)


def run_tool(home: Path, reference: Path, *extra):
    return subprocess.run(
        [sys.executable, str(TOOL), "--home", str(home), "--reference-src", str(reference),
         *extra],
        capture_output=True, text=True)


def published_lines(bundle: Path) -> list:
    """The block the evidence leg under a bundle publishes."""
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from %s import follow_through;"
        "print(chr(10).join(follow_through.lines(None)))" % (str(bundle), PACKAGE)
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_a_dry_run_writes_nothing_to_the_bundle(plant, reviewed):
    """A plan is a plan until it is applied."""
    home, bundle = plant
    before = digest_tree(bundle)
    result = run_tool(home, reviewed)
    assert result.returncode == 0, result.stdout + result.stderr
    assert digest_tree(bundle) == before
    assert "dry run" in result.stdout
    assert not list(home.glob("artifacts/observer-bundle-refresh/*"))


def carried_line(stdout: str) -> str:
    """The line that names what a refresh would carry."""
    for line in stdout.splitlines():
        if line.startswith("carried   :"):
            return line
    return ""


def test_the_plan_carries_the_sibling_and_the_stale_fact(plant, reviewed):
    """The refresh must carry the closure and the module that carries the facts."""
    home, bundle = plant
    result = run_tool(home, reviewed)
    assert result.returncode == 0, result.stdout + result.stderr
    carried = carried_line(result.stdout)
    assert "follow_through.py" in carried, result.stdout
    assert "wall.py" in carried, result.stdout
    assert "initiative.py" in carried, result.stdout


def test_applying_refreshes_the_bundle_and_verifies_it(plant, reviewed):
    """The written bundle runs and publishes the reviewed tree's facts."""
    home, bundle = plant
    result = run_tool(home, reviewed, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REFRESHED" in result.stdout
    assert digest_tree(bundle) == digest_tree(reviewed)
    assert published_lines(bundle) == published_lines(reviewed)


def test_applying_records_the_bytes_it_replaced(plant, reviewed):
    """The manifest names the bytes before and after, so a revert is possible."""
    home, bundle = plant
    before = digest_tree(bundle)
    result = run_tool(home, reviewed, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = Path([line.split("manifest  : ", 1)[1] for line in result.stdout.splitlines()
                     if line.startswith("manifest  : ")][0])
    record = json.loads(manifest.read_text())
    modules = {entry["module"] for entry in record["files"]}
    assert "follow_through.py" in modules
    for entry in record["files"]:
        assert entry["after_sha256"] == digest_tree(reviewed)[entry["module"]]
        if entry["module"] in before:
            assert entry["before_sha256"] == before[entry["module"]]
    assert Path(record["activation"]).is_file()


def test_the_record_names_the_reviewed_tree_even_without_a_repository(plant, reviewed):
    """An exported tree is not a repository, so the record names a digest too."""
    home, bundle = plant
    result = run_tool(home, reviewed, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = Path([line.split("manifest  : ", 1)[1] for line in result.stdout.splitlines()
                     if line.startswith("manifest  : ")][0])
    record = json.loads(manifest.read_text())
    parts = []
    for name in sorted(record["carried"]):
        path = reviewed / PACKAGE / name
        if path.is_file():
            parts.append(name + ":" + hashlib.sha256(path.read_bytes()).hexdigest())
    expected = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    assert record["reference_digest"] == expected
    name = Path(record["activation"]).name
    assert name.endswith(".json")
    assert "refresh-.json" not in name, name


def test_reverting_restores_the_previous_bytes(plant, reviewed):
    """A revert puts back exactly what was there, and the leg runs again."""
    home, bundle = plant
    before = digest_tree(bundle)
    lines_before = published_lines(bundle)
    applied = run_tool(home, reviewed, "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    manifest = Path([line.split("manifest  : ", 1)[1] for line in applied.stdout.splitlines()
                     if line.startswith("manifest  : ")][0])
    reverted = run_tool(home, reviewed, "--revert", str(manifest))
    assert reverted.returncode == 0, reverted.stdout + reverted.stderr
    assert "REVERTED" in reverted.stdout
    assert digest_tree(bundle) == before
    assert published_lines(bundle) == lines_before


def test_a_refresh_that_cannot_run_is_not_applied(plant, tmp_path):
    """A reviewed tree whose projection imports a name nothing provides is UNKNOWN."""
    home, bundle = plant
    reference = write_package(tmp_path / "reviewed", carries_name=True,
                               row=REVIEWED_ROW, projection_line=True)
    # The reviewed projection imports a name no module of the tree provides.
    (reference / PACKAGE / "follow_through.py").write_text(
        "from .wall import OUTCOME_REFUSAL_AUDIT\n"
        "from .initiative import ROWS\n"
        "from .wall import ABSENT_NAME\n"
        "LINES = []\n"
        "LINES.extend(ROWS)\n"
        "def lines(home):\n"
        "    return list(LINES)\n")
    before = digest_tree(bundle)
    result = run_tool(home, reference, "--apply")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout
    assert digest_tree(bundle) == before


def test_a_bundle_is_restored_when_the_verification_fails(plant, reviewed, monkeypatch):
    """A refresh that fails its verification leaves the plant as it was."""
    home, bundle = plant
    before = digest_tree(bundle)
    spec = importlib.util.spec_from_file_location("refresh_tool", TOOL)
    tool = importlib.util.module_from_spec(spec)
    sys.modules["refresh_tool"] = tool
    spec.loader.exec_module(tool)
    discovery = tool.load_discovery()

    class Failing:
        """The discovery, except that the plant's own bundle never runs."""

        def __getattr__(self, name):
            return getattr(discovery, name)

        def evidence_lines(self, root, home, cwd):
            if Path(root).resolve() == bundle.resolve():
                raise discovery.Undecidable("the refreshed bundle does not run")
            return discovery.evidence_lines(root, home, cwd)

    workdir = Path(reconciled_workdir())
    plan = tool.build_plan(Failing(), home, home.parent, reviewed, "facts", workdir)
    outcome = tool.apply_plan(Failing(), plan, home, home.parent, workdir)
    assert outcome["state"] == "ROLLED BACK", outcome
    assert digest_tree(bundle) == before
    assert published_lines(bundle) == [STALE_ROW]


def reconciled_workdir() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="refresh-observer-bundle-test-")


def test_a_bundle_is_restored_when_the_refreshed_facts_differ(plant, reviewed):
    """A refresh that runs but publishes the wrong facts is rolled back too."""
    home, bundle = plant
    before = digest_tree(bundle)
    spec = importlib.util.spec_from_file_location("refresh_tool_facts", TOOL)
    tool = importlib.util.module_from_spec(spec)
    sys.modules["refresh_tool_facts"] = tool
    spec.loader.exec_module(tool)
    discovery = tool.load_discovery()

    class Stale:
        """The discovery, except that the refreshed bundle publishes old facts."""

        def __getattr__(self, name):
            return getattr(discovery, name)

        def evidence_lines(self, root, home, cwd):
            if Path(root).resolve() == bundle.resolve():
                return [STALE_ROW]
            return discovery.evidence_lines(root, home, cwd)

    workdir = Path(reconciled_workdir())
    plan = tool.build_plan(Stale(), home, home.parent, reviewed, "facts", workdir)
    outcome = tool.apply_plan(Stale(), plan, home, home.parent, workdir)
    assert outcome["state"] == "ROLLED BACK", outcome
    assert digest_tree(bundle) == before
    assert published_lines(bundle) == [STALE_ROW]


def test_a_failure_while_pruning_restores_the_removed_files(plant, reviewed, monkeypatch):
    """A prune that fails half way must not leave the bundle short a module."""
    home, bundle = plant
    first = bundle / PACKAGE / "extra_one.py"
    second = bundle / PACKAGE / "extra_two.py"
    first.write_text("VALUE = 1\n")
    second.write_text("VALUE = 2\n")
    spec = importlib.util.spec_from_file_location("refresh_tool_prune", TOOL)
    tool = importlib.util.module_from_spec(spec)
    sys.modules["refresh_tool_prune"] = tool
    spec.loader.exec_module(tool)
    original = Path.unlink

    def failing_unlink(self, missing_ok=False):
        if self.name == "extra_two.py" and self.parent == bundle / PACKAGE:
            raise OSError("read-only file system")
        return original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    discovery = tool.load_discovery()
    workdir = Path(reconciled_workdir())
    plan = tool.build_plan(discovery, home, home.parent, reviewed, "facts", workdir, prune=True)
    outcome = tool.apply_plan(discovery, plan, home, home.parent, workdir, prune=True)
    assert outcome["state"] == "ROLLED BACK", outcome
    assert first.is_file(), "the module removed before the failure was not restored"
    assert first.read_text() == "VALUE = 1\n"
    assert second.is_file()


def test_a_module_the_reviewed_tree_does_not_carry_is_reported_and_kept(plant, reviewed):
    """A file the tree has no counterpart for is named, and a refresh keeps it."""
    home, bundle = plant
    (bundle / PACKAGE / "extra.py").write_text("VALUE = 1\n")
    result = run_tool(home, reviewed)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "extra.py" in result.stdout
    applied = run_tool(home, reviewed, "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (bundle / PACKAGE / "extra.py").is_file()
    assert (bundle / PACKAGE / "extra.py").read_text() == "VALUE = 1\n"


def test_pruning_removes_a_module_the_tree_does_not_carry_and_can_be_reverted(plant, reviewed):
    """Pruning is a change with a route back."""
    home, bundle = plant
    extra = bundle / PACKAGE / "extra.py"
    extra.write_text("VALUE = 1\n")
    result = run_tool(home, reviewed, "--apply", "--prune")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not extra.exists()
    manifest = Path([line.split("manifest  : ", 1)[1] for line in result.stdout.splitlines()
                     if line.startswith("manifest  : ")][0])
    reverted = run_tool(home, reviewed, "--revert", str(manifest))
    assert reverted.returncode == 0, reverted.stdout + reverted.stderr
    assert extra.is_file()
    assert extra.read_text() == "VALUE = 1\n"


def test_tree_mode_carries_the_whole_reviewed_package(plant, reviewed):
    """Tree mode makes the bundle's package the reviewed tree's package."""
    home, bundle = plant
    (bundle / PACKAGE / "stale_only.py").write_text("VALUE = 1\n")
    result = run_tool(home, reviewed, "--mode", "tree")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mode      : tree" in result.stdout
    carried = carried_line(result.stdout)
    for name in ("follow_through.py", "wall.py", "initiative.py"):
        assert name in carried, result.stdout
    assert "stale_only.py" in result.stdout


def test_a_charter_is_carried_like_a_module(plant, reviewed):
    """The package ships charters, and seed.py reads one at runtime."""
    home, bundle = plant
    charter = "seed_senses_charter.md"
    (bundle / PACKAGE / charter).write_text("old charter\n")
    (reviewed / PACKAGE / charter).write_text("new charter\n")
    result = run_tool(home, reviewed, "--mode", "tree")
    assert result.returncode == 0, result.stdout + result.stderr
    assert charter in carried_line(result.stdout)
    applied = run_tool(home, reviewed, "--mode", "tree", "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (bundle / PACKAGE / charter).read_text() == "new charter\n"


def test_a_file_the_bundle_lacks_is_added_and_a_revert_removes_it(plant, reviewed):
    """An added file has no previous bytes, so a revert must remove it."""
    home, bundle = plant
    (reviewed / PACKAGE / "added.py").write_text("VALUE = 7\n")
    applied = run_tool(home, reviewed, "--mode", "tree", "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (bundle / PACKAGE / "added.py").is_file()
    manifest = Path([line.split("manifest  : ", 1)[1] for line in applied.stdout.splitlines()
                     if line.startswith("manifest  : ")][0])
    record = json.loads(manifest.read_text())
    entry = [item for item in record["files"] if item["module"] == "added.py"]
    assert entry and entry[0]["before_sha256"] is None
    reverted = run_tool(home, reviewed, "--revert", str(manifest))
    assert reverted.returncode == 0, reverted.stdout + reverted.stderr
    assert not (bundle / PACKAGE / "added.py").exists()


def test_two_refreshes_in_the_same_second_keep_separate_backups(plant, reviewed, monkeypatch):
    """A second refresh must not overwrite the first one's route back."""
    home, bundle = plant
    spec = importlib.util.spec_from_file_location("refresh_tool_clock", TOOL)
    tool = importlib.util.module_from_spec(spec)
    sys.modules["refresh_tool_clock"] = tool
    spec.loader.exec_module(tool)
    discovery = tool.load_discovery()
    fixed = tool.datetime(2026, 10, 21, 2, 49, 29)

    class Clock:
        """A clock that does not move, so two refreshes share a second."""

        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(tool, "datetime", Clock)
    first_work = Path(reconciled_workdir())
    second_work = Path(reconciled_workdir())
    first = tool.apply_plan(discovery, tool.build_plan(discovery, home, home.parent, reviewed,
                                                       "facts", first_work),
                            home, home.parent, first_work)
    second = tool.apply_plan(discovery, tool.build_plan(discovery, home, home.parent, reviewed,
                                                        "facts", second_work),
                             home, home.parent, second_work)
    assert first["state"] == "REFRESHED" and second["state"] == "REFRESHED"
    assert first["manifest"] != second["manifest"], first["manifest"]
    assert first["backup"] != second["backup"]


def test_a_manifest_that_names_no_bundle_reads_unknown(plant, reviewed, tmp_path):
    """A foreign manifest is not a verdict."""
    home, bundle = plant
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"version": 1, "kind": "something else"}))
    result = run_tool(home, reviewed, "--revert", str(foreign))
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_revert_that_cannot_restore_a_module_says_so(plant, reviewed):
    """A module that could not be restored is a problem, not a success."""
    home, bundle = plant
    applied = run_tool(home, reviewed, "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    manifest = Path([line.split("manifest  : ", 1)[1] for line in applied.stdout.splitlines()
                     if line.startswith("manifest  : ")][0])
    (manifest.parent / "before" / "initiative.py").unlink()
    reverted = run_tool(home, reviewed, "--revert", str(manifest))
    assert reverted.returncode == 2, reverted.stdout + reverted.stderr
    assert "INCOMPLETE" in reverted.stdout
    assert "initiative.py" in reverted.stdout
    assert "no backup copy" in reverted.stdout, reverted.stdout


def test_the_manifest_exists_before_the_first_byte_is_written(plant, reviewed, monkeypatch):
    """A refresh that fails half way must still leave a record of the route back."""
    home, bundle = plant
    before = digest_tree(bundle)
    spec = importlib.util.spec_from_file_location("refresh_tool_manifest", TOOL)
    tool = importlib.util.module_from_spec(spec)
    sys.modules["refresh_tool_manifest"] = tool
    spec.loader.exec_module(tool)
    discovery = tool.load_discovery()
    original = tool.shutil.copy
    state = {"written": 0}

    def failing_copy(source, target, *args, **kwargs):
        if str(target).startswith(str(bundle)):
            state["written"] += 1
            if state["written"] == 2:
                raise OSError("no space left on device")
        return original(source, target, *args, **kwargs)

    monkeypatch.setattr(tool.shutil, "copy", failing_copy)
    workdir = Path(reconciled_workdir())
    plan = tool.build_plan(discovery, home, home.parent, reviewed, "facts", workdir)
    outcome = tool.apply_plan(discovery, plan, home, home.parent, workdir)
    assert outcome["state"] in ("ROLLED BACK", "ROLLBACK FAILED"), outcome
    manifest = Path(outcome["backup"]) / "manifest.json"
    assert manifest.is_file(), "the manifest must exist before the first byte is written"
    record = json.loads(manifest.read_text())
    assert record["files"]
    assert digest_tree(bundle) == before


def test_a_corrupt_backup_is_reported_rather_than_restored(plant, reviewed, tmp_path):
    """A rollback that cannot restore the recorded bytes must say so."""
    home, bundle = plant
    spec = importlib.util.spec_from_file_location("refresh_tool_corrupt", TOOL)
    tool = importlib.util.module_from_spec(spec)
    sys.modules["refresh_tool_corrupt"] = tool
    spec.loader.exec_module(tool)
    backup = tmp_path / "backup"
    (backup / "before").mkdir(parents=True)
    target = bundle / PACKAGE / "initiative.py"
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    (backup / "before" / "initiative.py").write_text("corrupted\n")
    files = [{"module": "initiative.py", "before_sha256": before}]
    problems = tool.restore(backup, files, [], bundle)
    assert problems, "a restore that cannot reproduce the recorded bytes must report it"
    assert "do not match" in problems[0], problems


def test_a_missing_bundle_reads_unknown(reviewed, tmp_path):
    """No bundle to refresh is not a plan."""
    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    result = run_tool(home, reviewed)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_missing_reviewed_tree_reads_unknown(plant):
    """No reviewed tree to refresh from is not a plan."""
    home, bundle = plant
    result = run_tool(home, home.parent / "absent")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def write_moving_package(root: Path, row: str = REVIEWED_ROW) -> Path:
    """A package whose published block carries a line that changes every run.

    The plant's own published block carries lines that move with the plant's
    state, so a difference between two samples is not yet a difference between
    two sets of bytes.
    """
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "wall.py").write_text("REFUSAL_AUDIT = 'logs/outcome-refusal-audit.jsonl'\n")
    (package / "initiative.py").write_text("ROWS = [%r]\n" % (row,))
    body = ["from .wall import REFUSAL_AUDIT",
            "from .initiative import ROWS",
            "import secrets",
            "",
            "LINES = []",
            "LINES.append('TOKEN: ' + secrets.token_hex(4))",
            "LINES.extend(ROWS)",
            "",
            "def lines(home):",
            "    return list(LINES)"]
    (package / "follow_through.py").write_text("\n".join(body) + "\n")
    return root


def test_a_published_line_that_moves_is_not_reported_as_a_difference(plant, tmp_path):
    """A line that changes between two samples is not a difference in the bytes."""
    home, bundle = plant
    shutil.rmtree(bundle / PACKAGE)
    write_moving_package(bundle)
    reviewed = write_moving_package(tmp_path / "moving-reviewed")
    result = run_tool(home, reviewed, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "facts     : AGREE" in result.stdout, result.stdout
    assert "moved     :" in result.stdout, result.stdout
    assert "state     : CURRENT" in result.stdout, result.stdout


def test_a_moving_line_does_not_hide_a_real_difference(plant, tmp_path):
    """A line that moves is excluded; a line that differs is still reported."""
    home, bundle = plant
    shutil.rmtree(bundle / PACKAGE)
    write_moving_package(bundle, row=STALE_ROW)
    reviewed = write_moving_package(tmp_path / "moving-reviewed", row=REVIEWED_ROW)
    result = run_tool(home, reviewed, "--check")
    assert result.returncode == 4, result.stdout + result.stderr
    assert "facts     : DIFFER" in result.stdout, result.stdout
    assert "state     : STALE" in result.stdout, result.stdout


# --- the observation: what the bundle's bytes are, against the reviewed tree's


def copy_package(source: Path, target: Path) -> Path:
    """A copy of a package directory, byte for byte."""
    shutil.copytree(source / PACKAGE, target / PACKAGE)
    return target


def state_line(stdout: str) -> str:
    """The line that names the observation's state."""
    for line in stdout.splitlines():
        if line.startswith("state     :"):
            return line
    return ""


def test_check_says_current_when_the_bundles_bytes_are_the_reviewed_trees(plant, reviewed):
    """A bundle that is the reviewed tree's package reads CURRENT."""
    home, bundle = plant
    shutil.rmtree(bundle / PACKAGE)
    copy_package(reviewed, bundle)
    result = run_tool(home, reviewed, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "state     : CURRENT" in result.stdout, result.stdout
    assert "bytes     : SAME" in result.stdout, result.stdout
    digests = [line for line in result.stdout.splitlines() if line.startswith("reference : sha256-")]
    assert digests, result.stdout


def test_check_names_the_bytes_that_differ_and_says_the_facts_agree(plant, reviewed):
    """Bytes that differ are named; a bundle that still publishes the facts is MIXED."""
    home, bundle = plant
    shutil.rmtree(bundle / PACKAGE)
    copy_package(reviewed, bundle)
    (bundle / PACKAGE / "__init__.py").write_text("# a comment the reviewed tree does not carry\n")
    result = run_tool(home, reviewed, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "state     : MIXED" in result.stdout, result.stdout
    assert "bytes     : DIFFER" in result.stdout, result.stdout
    assert "__init__.py" in result.stdout, result.stdout
    assert "facts     : AGREE" in result.stdout, result.stdout


def test_check_reports_stale_when_the_published_facts_differ(plant, reviewed):
    """A bundle that publishes facts the reviewed tree does not is STALE."""
    home, bundle = plant
    result = run_tool(home, reviewed, "--check")
    assert result.returncode == 4, result.stdout + result.stderr
    assert "state     : STALE" in result.stdout, result.stdout
    assert "facts     : DIFFER" in result.stdout, result.stdout


def test_check_names_a_file_the_reviewed_tree_does_not_carry(plant, reviewed):
    """A file only the bundle carries is part of the difference."""
    home, bundle = plant
    shutil.rmtree(bundle / PACKAGE)
    copy_package(reviewed, bundle)
    (bundle / PACKAGE / "extra.py").write_text("EXTRA = 1\n")
    result = run_tool(home, reviewed, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "state     : MIXED" in result.stdout, result.stdout
    assert "extra.py" in result.stdout, result.stdout


def test_check_writes_nothing_to_the_bundle(plant, reviewed):
    """The observation is read-only."""
    home, bundle = plant
    before = digest_tree(bundle)
    result = run_tool(home, reviewed, "--check")
    assert result.returncode == 4, result.stdout + result.stderr
    assert digest_tree(bundle) == before
    assert not list(home.glob("artifacts/observer-bundle-refresh/*"))


# --- the guard: a refresh that would leave the bundle with no reviewed commit


def git(repo: Path, *args) -> str:
    """Run a git command in the repository and return its output."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture
def repository(tmp_path):
    """A reviewed repository, a bundle that reproduces its first commit, and a home.

    The bundle is a copy of the repository's first commit, so the plant's own
    identity check can name a reviewed commit for it. The repository's second
    commit changes a module that carries no published fact, so a refresh that
    carries only the fact closure leaves the bundle a mixture of two commits.
    """
    repo = tmp_path / "repo"
    write_package(repo / "src", carries_name=False, row=STALE_ROW, projection_line=False)
    package = repo / "src" / PACKAGE
    (package / "notes.py").write_text("NOTE = 'old'\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=plant@example", "-c", "user.name=plant", "commit", "-qm", "one")
    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    bundle = tmp_path / "observer" / "src"
    shutil.copytree(repo / "src", bundle)
    write_renderer(home, bundle)
    return repo, home, bundle


def advance_repository(repo: Path) -> None:
    """Commit a reviewed tree whose facts agree and whose extra module differs."""
    package = repo / "src" / PACKAGE
    shutil.rmtree(package)
    write_package(repo / "src", carries_name=True, row=REVIEWED_ROW, projection_line=True)
    (package / "notes.py").write_text("NOTE = 'new'\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=plant@example", "-c", "user.name=plant", "commit", "-qm", "two")


def run_tool_in(home: Path, repo: Path, *extra):
    """Run the tool against a repository, with no reference named."""
    return subprocess.run(
        [sys.executable, str(TOOL), "--home", str(home), "--repo", str(repo), *extra],
        capture_output=True, text=True)


def test_a_refresh_that_would_leave_the_bundle_without_a_reviewed_commit_is_refused(repository):
    """The site's declared-root record names a commit, so a mixture is refused."""
    repo, home, bundle = repository
    advance_repository(repo)
    before = digest_tree(bundle)
    result = run_tool_in(home, repo, "--apply")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "identity  : REFUSED" in result.stdout, result.stdout
    assert "--mode tree" in result.stdout, result.stdout
    assert digest_tree(bundle) == before


def test_tree_mode_keeps_the_bundles_reviewed_commit(repository):
    """Carrying the whole reviewed package leaves the bundle reproducible."""
    repo, home, bundle = repository
    advance_repository(repo)
    result = run_tool_in(home, repo, "--mode", "tree", "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REFRESHED" in result.stdout, result.stdout
    assert (bundle / PACKAGE / "notes.py").read_text() == "NOTE = 'new'\n"
    assert published_lines(bundle) == published_lines(repo / "src")


def test_allow_mixture_accepts_the_refresh_the_tool_refused(repository):
    """A caller who means the mixture says so, and the refresh is written."""
    repo, home, bundle = repository
    advance_repository(repo)
    result = run_tool_in(home, repo, "--apply", "--allow-mixture")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REFRESHED" in result.stdout, result.stdout
    assert (bundle / PACKAGE / "notes.py").read_text() == "NOTE = 'old'\n"
    assert published_lines(bundle) == published_lines(repo / "src")
