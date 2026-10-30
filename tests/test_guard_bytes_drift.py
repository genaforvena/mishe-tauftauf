"""The host's guard bytes must be the bytes the repository names, and the plant must be able to see it.

The guard that latched the plant off on 2026-10-29 stayed off the host for a
day because nothing watched the relationship between the installed file and
the commits the repository names.  The committed fix was refused by the
install tool's exact-byte rule, and nothing said so until the plant was
already down.

These tests pin the detector that closes that gap.  Each one is written so
that deleting the behaviour it names makes it fail: the fault classes are
distinguished by their exit code and their reported state, not by a string
that happens to appear somewhere in a longer message.
"""
import datetime
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

CHECK = pathlib.Path(__file__).resolve().parents[1] / "checks" / "guard-bytes-drift.py"


def load_check():
    spec = importlib.util.spec_from_file_location("guard_bytes_drift", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo_with_pointer(tmp_path, payload: bytes) -> pathlib.Path:
    """A repository-shaped directory holding one guard pointer file."""
    commits = tmp_path / "tools" / "guard-activation" / "commits"
    commits.mkdir(parents=True)
    (commits / "install_commit.commit").write_bytes(payload)
    return tmp_path


def stub_evidence(monkeypatch, check, *, installed, install=b"install bytes\n",
                  revert=b"revert bytes\n", started=None, written=None):
    """Give the check a complete, controlled set of evidence."""
    monkeypatch.setattr(check, "_pointer", lambda name, repo=None: "commit-" + name)
    monkeypatch.setattr(check, "_source_at",
                        lambda commit, repo=None: install if "install" in commit else revert)
    monkeypatch.setattr(check, "_installed_bytes", lambda: installed)
    now = datetime.datetime.now(datetime.timezone.utc)
    monkeypatch.setattr(check, "_main_pid", lambda: 4242)
    monkeypatch.setattr(check, "_process_started",
                        lambda pid: started if started is not None else now)
    monkeypatch.setattr(check, "_installed_written",
                        lambda: written if written is not None else now)


def test_the_install_target_bytes_are_reported_current(monkeypatch):
    """The healthy state: the host carries the install target and runs it."""
    check = load_check()
    stub_evidence(monkeypatch, check, installed=b"install bytes\n")
    row = check.assess()
    assert row["state"] == "current"


def test_bytes_that_match_neither_pointer_are_drift(monkeypatch):
    """The class of failure that kept the committed fix off the host for a day."""
    check = load_check()
    stub_evidence(monkeypatch, check, installed=b"some third party's bytes\n")
    row = check.assess()
    assert row["state"] == "drift"


def test_drift_is_a_fault_and_current_is_not(monkeypatch):
    """The exit code has to separate the fault from the healthy state."""
    check = load_check()
    stub_evidence(monkeypatch, check, installed=b"some third party's bytes\n")
    assert check.main(["--json"]) == 2
    stub_evidence(monkeypatch, check, installed=b"install bytes\n")
    assert check.main(["--json"]) == 0


def test_the_revert_target_is_a_state_and_not_a_fault(monkeypatch):
    """The backout path exists, so the revert target must not read as a fault."""
    check = load_check()
    stub_evidence(monkeypatch, check, installed=b"revert bytes\n")
    row = check.assess()
    assert row["state"] == "reverted"
    assert check.main(["--json"]) == 0


def test_the_install_target_on_disk_but_not_loaded_is_not_activated(monkeypatch):
    """A replaced file is not a running boundary: the sentinel may predate the bytes."""
    check = load_check()
    now = datetime.datetime.now(datetime.timezone.utc)
    stub_evidence(monkeypatch, check, installed=b"install bytes\n",
                  started=now - datetime.timedelta(seconds=60),
                  written=now)
    row = check.assess()
    assert row["state"] == "not-activated"
    assert check.main(["--json"]) == 2


def test_a_sentinel_that_started_after_the_bytes_was_written_is_activated(monkeypatch):
    check = load_check()
    now = datetime.datetime.now(datetime.timezone.utc)
    stub_evidence(monkeypatch, check, installed=b"install bytes\n",
                  started=now + datetime.timedelta(seconds=60),
                  written=now)
    assert check.assess()["state"] == "current"


def test_an_unreadable_guard_is_unknown_and_not_a_fault(monkeypatch, capsys):
    """A sudo failure is a privilege problem, not wrong bytes."""
    check = load_check()

    def refuse():
        raise check.Undecidable("the installed guard could not be read as root")

    monkeypatch.setattr(check, "_installed_bytes", refuse)
    assert check.main([]) == 3
    assert "UNKNOWN" in capsys.readouterr().out


def test_an_unreadable_pointer_is_unknown(monkeypatch):
    """A pointer that names nothing is the fault apply.py refuses on, and it is UNKNOWN here."""
    check = load_check()

    def refuse(name, repo=None):
        raise check.Undecidable("cannot read the %s pointer" % name)

    monkeypatch.setattr(check, "_pointer", refuse)
    with pytest.raises(check.Undecidable):
        check.assess()


def test_an_empty_pointer_file_is_unknown(monkeypatch, tmp_path):
    """An empty pointer names no commit, so the question cannot be decided."""
    check = load_check()
    repo = _repo_with_pointer(tmp_path, b"\n")
    with pytest.raises(check.Undecidable):
        check._pointer("install_commit", repo)


def test_a_pointer_with_a_trailing_newline_still_names_its_commit(monkeypatch, tmp_path):
    """apply.py passes the pointer bytes straight to git show, so a newline would break it."""
    check = load_check()
    repo = _repo_with_pointer(tmp_path, b"6cd0799\n")
    assert check._pointer("install_commit", repo) == "6cd0799"


def test_a_commit_that_cannot_be_read_is_unknown(monkeypatch):
    """A pointer naming a commit the repository does not carry is UNKNOWN, not drift."""
    check = load_check()

    def refuse(commit, repo=None):
        raise check.Undecidable("cannot read host_containment.py at commit " + commit)

    monkeypatch.setattr(check, "_source_at", refuse)
    with pytest.raises(check.Undecidable):
        check.assess()


def test_the_verdict_records_both_named_targets(monkeypatch):
    """A reader has to be able to see what the repository names, not just that something is wrong."""
    check = load_check()
    stub_evidence(monkeypatch, check, installed=b"install bytes\n")
    row = check.assess()
    assert row["install_commit"] == "commit-install_commit"
    assert row["revert_commit"] == "commit-revert_commit"
    assert row["install_sha256"] != row["revert_sha256"]


def test_the_verdict_can_be_written_as_state(monkeypatch, tmp_path):
    """The plant's monitor needs the verdict as a file it can read back."""
    check = load_check()
    stub_evidence(monkeypatch, check, installed=b"install bytes\n")
    state = tmp_path / "guard-bytes-drift.json"
    assert check.main(["--state", str(state)]) == 0
    row = json.loads(state.read_text(encoding="utf-8"))
    assert row["state"] == "current"


def test_an_unknown_verdict_is_written_as_unknown(monkeypatch, tmp_path):
    """A missing guard must not leave a stale green verdict behind for the monitor to read."""
    check = load_check()

    def refuse():
        raise check.Undecidable("no guard is installed")

    monkeypatch.setattr(check, "_installed_bytes", refuse)
    state = tmp_path / "guard-bytes-drift.json"
    assert check.main(["--state", str(state)]) == 3
    row = json.loads(state.read_text(encoding="utf-8"))
    assert row["state"] == "UNKNOWN"


def test_the_detector_runs_against_the_committed_tree(monkeypatch):
    """The check names real commits, so the pointers it reads must resolve.

    On a machine with no guard installed the check reports UNKNOWN, which is
    a plain line rather than JSON, so the verdict is read from whichever form
    the check produced.  The point of this test is that the two named targets
    are real commits in this repository and that the check runs to a decision
    rather than raising.
    """
    result = subprocess.run([sys.executable, str(CHECK), "--json"], capture_output=True, text=True)
    assert result.returncode in (0, 2, 3), result.stderr
    row = json.loads(result.stdout)
    assert row["state"] in ("current", "drift", "not-activated", "reverted", "UNKNOWN")
    if row["state"] in ("current", "not-activated"):
        # Both named targets must be real commits in this repository.
        for commit in (row["install_commit"], row["revert_commit"]):
            shown = subprocess.run(["git", "-C", str(CHECK.parents[1]), "cat-file", "-e",
                                    commit + "^{commit}"], capture_output=True)
            assert shown.returncode == 0, commit


def test_the_unknown_path_is_machine_readable(monkeypatch, capsys):
    """A machine with no guard gets a readable UNKNOWN verdict, and exit 3.

    The JSON form is promised in both cases: a monitor reading this check's
    output must not have to parse prose to learn whether the question was
    decided, and a CI runner must be able to read the same field either way.
    """
    check = load_check()

    def refuse():
        raise check.Undecidable("no guard is installed at /usr/local/lib/mishe-containment/guard.py")

    monkeypatch.setattr(check, "_installed_bytes", refuse)
    assert check.main(["--json"]) == 3
    row = json.loads(capsys.readouterr().out)
    assert row["state"] == "UNKNOWN"
    assert "no guard is installed" in row["reason"]
