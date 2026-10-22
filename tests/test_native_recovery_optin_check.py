"""The recovery opt-in detector must name the wake the plant will not deliver.

``checks/native-recovery-optin.py`` reads a role launcher's recovery opt-in
and the wake the plant still owes that role. The opt-in lives in a file the
integration owner edits by hand, and nothing in the plant reads the mismatch:
measured on 2026-10-21, ``minds/docs`` named wake 70477 while the plant owed
docs wake 75277. The recovery the owner wanted did not happen and the hold
that replaced it named no cause.

These tests pin the detector's decisions on synthetic sites, so the readings
are fixed by the fixture rather than by the machine:

* an opt-in that names the pending wake reads agreement, and a launcher
  without an opt-in is not a finding;
* an opt-in that names another wake while one is pending reads a mismatch,
  naming both wakes, and so does one that spells the number differently, names
  a text that is not a number, or names no wake at all;
* an opt-in with a shell comment is read as the value the shell would export;
* a finding that needs only the launcher's bytes survives a plant whose state
  cannot be read;
* the consumer's comparison is cited only when its operands are the opt-in and
  a stringified wake;
* the detector decides with the reviewed tree, not with the working tree;
* a launcher, a tape or a reviewed tree that cannot be read reads UNKNOWN,
  never a verdict, and never a traceback;
* the check writes nothing into the site it reads, and it does not decide the
  question it deliberately leaves open.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from mishe_tauftauf.feed import Feed

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "native-recovery-optin.py"
ROLE = "docs"
RECORD_DIGEST = "0" * 64

LAUNCHER = """#!/bin/sh
export MISHE_NATIVE_RECOVERY_WAKE={wake}
export MISHE_NATIVE_RECOVERY_ATTEMPT={attempt}
{runner}
"""

# A stand-in for the recovery consumer: the shape the detector reads — the
# opt-in taken out of the environment and compared with a stringified wake.
# The real runner calls its own worker from the module's main block, so the
# stand-in calls it too: a conversion on a path the module never takes is not
# the consumer's rule.
RUNNER = '''"""A stand-in for the recovery runner, with the bytes the plant runs."""
import os


def run_wake(wake):
    recovery = os.environ.get("MISHE_NATIVE_RECOVERY_WAKE") == str(wake)
    number = int(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))
    return recovery, number


if __name__ == "__main__":
    run_wake(1)
'''

# A minimal plant package, enough for the detector's reviewed readers, used
# to prove that the detector decides with the committed tree and not with the
# working tree's bytes.
SYNTHETIC = {
    "activity.py": "def roles(home):\n    return ['docs']\n",
    "seed.py": ("def _state(home, role, entries=None):\n"
                "    return (None, PENDING, None, None)\n"),
    "feed.py": (
        "from pathlib import Path\n\n\n"
        "class Feed:\n"
        "    def __init__(self, home):\n"
        "        self.home = Path(home)\n\n"
        "    def read_bytes(self):\n"
        "        return b''\n\n"
        "    @staticmethod\n"
        "    def _verify_records(entries, sink=None):\n"
        "        return None\n\n\n"
        "def parse_feed(data, *, start_sequence=1, home=None):\n"
        "    return []\n"
    ),
}


def make_home(tmp_path: Path, *, launcher: str | None = None) -> Path:
    """A site home with a launcher and a tape the plant can read."""
    home = tmp_path / "site"
    (home / "minds").mkdir(parents=True)
    (home / "top-pains").mkdir(parents=True)
    if launcher is not None:
        (home / "minds" / ROLE).write_text(launcher, encoding="utf-8")
    return home


def wake(home: Path) -> int:
    """Deliver a wake to the role; return the plant's number for it."""
    entry = Feed(home).append("seed", "seed wake %s observation=1\nOwed work.\n" % ROLE)
    return entry.sequence


def settle(home: Path, number: int) -> None:
    """Settle a delivered wake."""
    Feed(home).append("seed", "seed yield %s wake=%d\nTurn settled (changed); done.\n"
                       % (ROLE, number))


def run_check(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(home),
                           "--repo", str(REPO), *args],
                          capture_output=True, text=True, timeout=300)


def launcher_text(wake_number: int, attempt: int = 1, runner: str = "") -> str:
    return LAUNCHER.format(wake=wake_number, attempt=attempt, runner=runner)


def runner_line(home: Path, source: str = RUNNER) -> str:
    """A launcher that runs a fixture consumer."""
    runner = home / "runner.py"
    runner.write_text(source, encoding="utf-8")
    return "exec %s" % runner


def site_state(home: Path) -> str:
    """The site's own bytes, sizes and times, so a check that writes into it is caught."""
    state = hashlib.sha256()
    for path in sorted(home.rglob("*")):
        info = path.stat()
        state.update(str(path.relative_to(home)).encode("utf-8"))
        state.update(b"\0")
        state.update(("%d %d" % (info.st_size, info.st_mtime_ns)).encode("utf-8"))
        state.update(b"\0")
        if path.is_file():
            state.update(path.read_bytes())
        state.update(b"\0")
    return state.hexdigest()


def synthetic_repo(tmp_path: Path, pending: str) -> Path:
    """A repository whose committed tree reports one pending wake."""
    repo = tmp_path / "repo"
    package = repo / "src" / "mishe_tauftauf"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, body in SYNTHETIC.items():
        text = body.replace("PENDING", pending) if name == "seed.py" else body
        (package / name).write_text(text, encoding="utf-8")
    environment = dict(os.environ, GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example",
                       GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "reviewed tree"],
                   check=True, capture_output=True, env=environment)
    # The working tree now says something else; the committed tree does not.
    (package / "seed.py").write_text(SYNTHETIC["seed.py"].replace("PENDING", "999"),
                                     encoding="utf-8")
    return repo


def test_opt_in_naming_the_pending_wake_reads_agreement(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(launcher_text(number), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "AGREE" in result.stdout


def test_a_launcher_without_an_opt_in_is_not_a_finding(tmp_path):
    home = make_home(tmp_path, launcher="#!/bin/sh\nexport MISHE_NATIVE_SELECTOR=x\n")
    wake(home)
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "MISMATCH" not in result.stdout
    # The check reports the launchers that carry an opt-in, and no others.
    assert str(home / "minds" / ROLE) not in result.stdout


def test_opt_in_naming_another_wake_reads_a_mismatch(tmp_path):
    home = make_home(tmp_path)
    settled = wake(home)
    settle(home, settled)
    pending = wake(home)
    (home / "minds" / ROLE).write_text(launcher_text(settled), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "MISMATCH" in result.stdout
    assert "the opt-in names wake %d" % settled in result.stdout
    assert "the plant owes %s wake %d" % (ROLE, pending) in result.stdout
    assert str(home / "minds" / ROLE) in result.stdout


def test_an_opt_in_that_spells_the_number_differently_reads_a_mismatch(tmp_path):
    """The consumer compares texts, so a spelled-different number is not the wake."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE=0%d" % number),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "MISMATCH" in result.stdout
    assert "the opt-in names wake 0%d" % number in result.stdout


def test_an_opt_in_that_names_a_text_reads_a_mismatch(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE=seven"), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "not a wake number" in result.stdout


def test_opt_in_naming_a_wake_the_plant_does_not_owe_reads_a_mismatch(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    settle(home, number)
    (home / "minds" / ROLE).write_text(launcher_text(number), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "MISMATCH" in result.stdout
    assert "owes %s no wake" % ROLE in result.stdout


def test_an_attempt_number_without_a_wake_reads_a_mismatch(tmp_path):
    home = make_home(tmp_path, launcher="#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_ATTEMPT=1\n")
    wake(home)
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "MISMATCH" in result.stdout
    assert "without naming the" in result.stdout


def test_a_launcher_finding_survives_a_plant_whose_state_cannot_be_read(tmp_path):
    home = make_home(tmp_path, launcher="#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_ATTEMPT=1\n")
    wake(home)
    result = run_check(home, "--repo", str(tmp_path))
    assert result.returncode == 2, result.stdout
    assert "MISMATCH" in result.stdout
    assert "UNKNOWN" in result.stdout


def test_an_empty_opt_in_value_does_not_consume_the_next_line(tmp_path):
    home = make_home(tmp_path, launcher="#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_WAKE=\n"
                                         "export MISHE_NATIVE_RECOVERY_ATTEMPT=1\n")
    wake(home)
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "opt-in    : wake= attempt=1" in result.stdout
    assert "not a wake number" in result.stdout


def test_a_quoted_opt_in_value_is_read_as_the_wake_it_names(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE='%d'" % number),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "opt-in    : wake=%d attempt=1" % number in result.stdout


def test_an_opt_in_with_a_shell_comment_is_read_as_the_shell_reads_it(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE=%d # the wake to recover" % number),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "opt-in    : wake=%d attempt=1" % number in result.stdout


def test_the_consumer_comparison_is_cited_from_its_own_bytes(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number, runner=runner_line(home)), encoding="utf-8")
    source = (home / "runner.py").read_text(encoding="utf-8")
    line = next(index for index, text in enumerate(source.splitlines(), 1)
                if "MISHE_NATIVE_RECOVERY_WAKE" in text and "==" in text)
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert str(home / "runner.py") in result.stdout
    assert "compares the opt-in with the pending wake at line %d" % line in result.stdout


def test_a_consumer_that_compares_the_opt_in_with_something_else_is_not_cited(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    other = RUNNER.replace('os.environ.get("MISHE_NATIVE_RECOVERY_WAKE") == str(wake)',
                            'os.environ.get("MISHE_NATIVE_RECOVERY_WAKE") == "1"')
    (home / "minds" / ROLE).write_text(
        launcher_text(number, runner=runner_line(home, other)), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "no comparison of the opt-in with a wake found" in result.stdout


def test_the_detector_decides_with_the_committed_tree_not_the_working_tree(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(launcher_text(number), encoding="utf-8")
    repo = synthetic_repo(tmp_path, pending="1")
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(home),
                             "--repo", str(repo)], capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout
    assert "pending   : 1" in result.stdout
    # The sentinel is anchored to the field it means. The check prints the
    # reviewed tree's commit SHA and the temporary home's path beside the
    # verdict, and either can contain "999" by chance — a SHA does so for
    # roughly one run in a hundred — so a scan of the whole stream fails
    # although the check read the committed tree exactly as it should.
    assert "pending   : 999" not in result.stdout


def test_an_unreadable_tape_reads_unknown(tmp_path):
    home = make_home(tmp_path, launcher=launcher_text(1))
    wake(home)
    with (home / "chat.log").open("a", encoding="utf-8") as handle:
        handle.write("v1 00000000000000009999 2026-10-21T00:00:00.000000Z seed ::\n"
                     "    | seed wake docs\n"
                     "    .-\n")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert result.stdout.startswith("UNKNOWN")
    assert "Traceback" not in result.stdout + result.stderr


def test_a_malformed_lifecycle_sentence_reads_unknown(tmp_path):
    home = make_home(tmp_path, launcher=launcher_text(1))
    wake(home)
    frames = sum(1 for line in (home / "chat.log").read_text(encoding="utf-8").splitlines()
                 if line.startswith("v1 "))
    with (home / "chat.log").open("a", encoding="utf-8") as handle:
        handle.write("v1 %020d 2026-10-21T00:00:00.000000Z seed ::\n"
                     "    | seed wake docs\n"
                     "    | prose\n"
                     "    .-\n" % (frames + 1))
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_a_tape_whose_record_cannot_be_resolved_reads_unknown(tmp_path):
    home = make_home(tmp_path, launcher=launcher_text(1))
    wake(home)
    # The frame is validly framed and contiguous, so the tape parses; the
    # record it references does not exist, so the reviewed verification of the
    # tape's records rejects it.
    frames = sum(1 for line in (home / "chat.log").read_text(encoding="utf-8").splitlines()
                 if line.startswith("v1 "))
    with (home / "chat.log").open("a", encoding="utf-8") as handle:
        handle.write("v1 %020d 2026-10-21T00:00:00.000000Z seed ::\n"
                     "    | [record] records/%s.json sha256=%s\n"
                     "    | prose\n"
                     "    .-\n" % (frames + 1, RECORD_DIGEST, RECORD_DIGEST))
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_an_unreadable_launcher_reads_unknown(tmp_path):
    home = make_home(tmp_path)
    wake(home)
    (home / "minds" / ROLE).write_bytes(b"#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_WAKE=\xff\xfe\n")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout
    assert "launcher is unreadable" in result.stdout


def test_a_launcher_that_cannot_be_read_reads_unknown(tmp_path):
    home = make_home(tmp_path)
    wake(home)
    (home / "minds" / ROLE).mkdir()
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout
    assert "launcher is unreadable" in result.stdout


def test_a_missing_reviewed_tree_reads_unknown(tmp_path):
    home = make_home(tmp_path, launcher=launcher_text(1))
    wake(home)
    empty = tmp_path / "empty-repo"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", str(empty)], check=True, capture_output=True)
    result = run_check(home, "--repo", str(empty))
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_an_unknown_role_reads_unknown(tmp_path):
    home = make_home(tmp_path, launcher=launcher_text(1))
    wake(home)
    result = run_check(home, "--role", "no-such-role")
    assert result.returncode == 3, result.stdout
    assert "not a role the reviewed tree knows" in result.stdout


def test_the_check_does_not_decide_the_collision_question(tmp_path):
    """The attempt directory is the consumer's own business; the check leaves it."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(launcher_text(number), encoding="utf-8")
    used = home / "native-canary" / ROLE / "wakes" / ("%d-recovery-1" % number)
    used.mkdir(parents=True)
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "REDELIVERY" not in result.stdout
    assert "collision" not in result.stdout.lower()


def test_a_temporary_directory_inside_the_site_home_reads_unknown(tmp_path):
    home = make_home(tmp_path, launcher=launcher_text(1))
    wake(home)
    inside = home / "tmp"
    inside.mkdir()
    environment = dict(os.environ, TMPDIR=str(inside))
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(home),
                             "--repo", str(REPO)], capture_output=True, text=True,
                            timeout=300, env=environment)
    assert result.returncode == 3, result.stdout
    assert "the temporary directory is inside the site home" in result.stdout
    assert list(inside.iterdir()) == []


def test_the_check_writes_nothing_into_the_site_it_reads(tmp_path):
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number, runner=runner_line(home)), encoding="utf-8")
    # The fixture's own writer left a checkpoint behind; the check must not
    # need one, and must not create one.
    (home / "feed.index.json").unlink(missing_ok=True)
    before = site_state(home)
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert site_state(home) == before
    assert not (home / "feed.index.json").exists()


def fake_shell(tmp_path: Path, name: str, body: str) -> Path:
    """A shell the check can ask, so its answer does not depend on the host."""
    shell = tmp_path / name
    shell.write_text(body, encoding="utf-8")
    shell.chmod(0o755)
    return shell


def test_a_launcher_the_plant_knows_no_role_for_reads_unknown(tmp_path):
    """An opt-in in a launcher the plant knows no role for is not an agreement."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / "docs-native").write_text(launcher_text(number), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout
    assert "AGREE" not in result.stdout
    assert "knows no role docs-native" in result.stdout


def test_the_role_comes_from_the_consumer_not_from_the_file_name(tmp_path):
    """The consumer declares the role it serves; the file name may differ."""
    home = make_home(tmp_path)
    number = wake(home)
    source = "ROLE = 'docs'\n" + RUNNER
    (home / "minds" / "docs-native").write_text(
        launcher_text(number, runner=runner_line(home, source)), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "role      : docs" in result.stdout


def test_an_export_followed_by_a_metacharacter_reads_agreement(tmp_path):
    """`export NAME=value && export OTHER=1` is two commands, not a bad name.

    The shell ends the export at the metacharacter and exports the value; a
    reader that treated the rest as another name to export would name a
    correct launcher as broken.
    """
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        "#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_WAKE=%d && export MISHE_NATIVE_RECOVERY_ATTEMPT=1\n"
        % number, encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout


def test_an_export_with_two_names_reads_agreement(tmp_path):
    """`export NAME=value OTHER=1` exports both names and reports no error."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        "#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_WAKE=%d MISHE_NATIVE_RECOVERY_ATTEMPT=1\n"
        % number, encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout


def test_an_export_ended_by_a_semicolon_reads_agreement(tmp_path):
    """`export NAME=value; export OTHER=1` is two commands, not a bad name."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        "#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_WAKE=%d; export MISHE_NATIVE_RECOVERY_ATTEMPT=1\n"
        % number, encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout


def test_a_metacharacter_inside_quotes_is_part_of_the_value(tmp_path):
    """`export NAME='7;7'` exports a value the shell quotes; the `;` is not a break."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        "#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_WAKE='%d;%d'\n" % (number, number),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "MISMATCH" in result.stdout


def test_a_word_the_shell_refuses_as_a_name_still_reads_a_mismatch(tmp_path):
    """`export NAME=value 2` is the shell's bad variable name, and stays a finding."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        "#!/bin/sh\nexport MISHE_NATIVE_RECOVERY_WAKE=%d 2\n" % number, encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "MISMATCH" in result.stdout


def test_an_export_the_shell_rejects_reads_a_mismatch(tmp_path):
    """`export NAME = value` names three words; the shell exports no value."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE = %d" % number),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "MISMATCH" in result.stdout
    assert "the shell rejects" in result.stdout


def test_an_export_the_shell_ends_at_reads_a_mismatch(tmp_path):
    """A shell that stops at the rejected export never starts the resident."""
    home = make_home(tmp_path)
    number = wake(home)
    shell = fake_shell(tmp_path, "ends", "#!/bin/sh\nexit 2\n")
    (home / "minds" / ROLE).write_text(
        "#!%s\nexport MISHE_NATIVE_RECOVERY_WAKE=%d 2\n" % (shell, number),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "ends the launcher" in result.stdout


def test_an_export_the_shell_continues_past_reads_unknown(tmp_path):
    """A shell that continues may still export the value, so the reading is unknown."""
    home = make_home(tmp_path)
    number = wake(home)
    shell = fake_shell(tmp_path, "continues",
                       "#!/bin/sh\n/bin/sh -c \"$2\" >/dev/null 2>&1 || true\nprintf REACHED\n")
    (home / "minds" / ROLE).write_text(
        "#!%s\nexport MISHE_NATIVE_RECOVERY_WAKE=%d 2\n" % (shell, number),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "only if the shell continues" in result.stdout


def test_an_expanding_opt_in_reads_unknown(tmp_path):
    """The consumer would see the expansion; the check does not read the environment."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE=$MISHE_TEST_WAKE"),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "expands" in result.stdout


def test_an_opt_in_that_is_only_assigned_reads_unknown(tmp_path):
    """An assignment does not export; the consumer may see an inherited value."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        "#!/bin/sh\nMISHE_NATIVE_RECOVERY_WAKE=%d\n" % number, encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "not exported" in result.stdout


def test_a_wake_that_is_not_an_ascii_number_reads_a_mismatch(tmp_path):
    """`²` is a digit to `str.isdigit` and not a number to `int()`."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE=²"), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "not a wake number" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_a_unicode_decimal_digit_is_not_a_wake_number(tmp_path):
    """`٣` is a digit to `\\d` and to `int()`; the plant's wakes are ASCII."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE=٣"), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "not a wake number" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_a_wake_of_zero_reads_a_mismatch(tmp_path):
    """Wake 0 is a number and not a wake; the plant's wakes count from one."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number, "WAKE=0"), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "not a wake number" in result.stdout


def test_an_attempt_the_consumer_cannot_read_reads_a_mismatch(tmp_path):
    """The consumer reads the attempt with int(); the check reads that rule."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home)), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "int()" in result.stdout


def test_the_last_export_of_a_name_decides(tmp_path):
    """The shell exports the variable's current value, so the last export wins."""
    home = make_home(tmp_path)
    number = wake(home)
    (home / "minds" / ROLE).write_text(
        launcher_text(number).replace("WAKE=%d" % number,
                                      "WAKE=1\nMISHE_NATIVE_RECOVERY_WAKE=%d" % number),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "opt-in    : wake=%d attempt=1" % number in result.stdout


def test_an_attempt_read_with_a_subscript_reads_a_mismatch(tmp_path):
    """``os.environ[NAME]`` reads the attempt as surely as ``os.environ.get``."""
    home = make_home(tmp_path)
    number = wake(home)
    source = RUNNER.replace(
        'int(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))',
        'int(os.environ["MISHE_NATIVE_RECOVERY_ATTEMPT"])')
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "int()" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_an_attempt_read_into_a_name_and_converted_afterwards_reads_a_mismatch(tmp_path):
    """The conversion may be a step after the read; the rule is still the consumer's."""
    home = make_home(tmp_path)
    number = wake(home)
    source = RUNNER.replace(
        'number = int(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))',
        'raw = os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1")\n'
        '    number = int(raw)')
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "int()" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_a_conversion_the_consumer_survives_is_not_a_mismatch(tmp_path):
    """A consumer that catches the error can read a text attempt; that is not a defect."""
    home = make_home(tmp_path)
    number = wake(home)
    source = RUNNER.replace(
        'number = int(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))',
        'try:\n'
        '        number = int(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))\n'
        '    except ValueError:\n'
        '        number = 1')
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "survives the failure" in result.stdout


def test_a_conversion_the_module_never_reaches_is_not_a_mismatch(tmp_path):
    """A conversion in a function the module never calls is not the consumer's rule."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer whose conversion is on a path the module never takes."""
import os


def never_called():
    return int(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "a function the module never calls" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_an_attempt_passed_into_a_function_and_converted_there_reads_a_mismatch(tmp_path):
    """The consumer passes the attempt to its own function and converts it there."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer that converts the attempt in a function of its own."""
import os


def worker(attempt):
    return int(attempt)


def run_wake(wake):
    return worker(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))


run_wake(1)
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "int()" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_an_attempt_passed_on_a_path_the_module_does_not_take_reads_unknown(tmp_path):
    """A call on a path the module may not take is a flow the check cannot trace."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer that passes the attempt on one path only."""
import os


def worker(attempt):
    return int(attempt)


def run_wake(wake):
    if True:
        return worker(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))
    return 1


run_wake(1)
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "cannot be traced back to the attempt" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_a_conversion_the_callee_survives_is_not_a_mismatch(tmp_path):
    """A callee that catches the error can read a text attempt; that is not a defect."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer whose callee survives a failed conversion."""
import os


def worker(attempt):
    try:
        return int(attempt)
    except ValueError:
        return 1


def run_wake(wake):
    return worker(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))


run_wake(1)
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "survives the failure" in result.stdout


def test_a_conversion_in_a_callee_the_module_never_calls_is_not_a_mismatch(tmp_path):
    """A callee the module never calls is not a path the module takes."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer whose callee is never called."""
import os


def worker(attempt):
    return int(attempt)


def run_wake(wake):
    return 1


run_wake(1)
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "a function the module never calls" in result.stdout


def test_an_attempt_passed_to_a_parameter_the_conversion_does_not_read_is_not_a_mismatch(tmp_path):
    """The attempt reaches the call, but not the value the conversion reads."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer that converts a value the attempt does not reach."""
import os


def worker(first, second):
    return int(second)


def run_wake(wake):
    return worker(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"), 2)


run_wake(1)
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_an_attempt_passed_through_a_method_call_reads_unknown(tmp_path):
    """A call through an attribute does not name the definition it reaches."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer that converts the attempt in a method of its own class."""
import os


class Consumer:
    def run(self):
        self.convert(os.environ["MISHE_NATIVE_RECOVERY_ATTEMPT"])

    def convert(self, value):
        return int(value)


consumer = Consumer()
consumer.run()
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "dispatched through an attribute" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_an_attempt_passed_to_a_method_through_the_class_reads_unknown(tmp_path):
    """A call that names the class passes the instance, so the value sits later."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer that calls its own method through the class."""
import os


class Consumer:
    def convert(self, value):
        return int(value)


consumer = Consumer()
Consumer.convert(consumer, os.environ["MISHE_NATIVE_RECOVERY_ATTEMPT"])
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "dispatched through an attribute" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_a_method_call_that_carries_no_attempt_is_not_an_unknown(tmp_path):
    """A method call that carries something else is not a flow of the attempt."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer whose method call carries no attempt."""
import os


class Consumer:
    def run(self):
        self.convert(2)

    def convert(self, value):
        return int(value)


consumer = Consumer()
consumer.run()
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "MISMATCH" not in result.stdout
    assert "UNKNOWN" not in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_a_resolvable_call_reads_the_defect_next_to_a_method_call(tmp_path):
    """A flow the reader can follow decides, whatever else the module does."""
    home = make_home(tmp_path)
    number = wake(home)
    source = '''"""A consumer with a resolvable call and a call through an attribute."""
import os


def worker(attempt):
    return int(attempt)


class Consumer:
    def run(self):
        self.convert(os.environ["MISHE_NATIVE_RECOVERY_ATTEMPT"])

    def convert(self, value):
        return int(value)


def run_wake(wake):
    return worker(os.environ.get("MISHE_NATIVE_RECOVERY_ATTEMPT", "1"))


run_wake(1)
'''
    (home / "minds" / ROLE).write_text(
        launcher_text(number, attempt="one", runner=runner_line(home, source)),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "int()" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_a_consumer_that_compares_with_a_subscript_read_is_cited(tmp_path):
    """The opt-in read with a subscript is the same comparison, and it is cited."""
    home = make_home(tmp_path)
    number = wake(home)
    source = RUNNER.replace(
        'os.environ.get("MISHE_NATIVE_RECOVERY_WAKE") == str(wake)',
        'os.environ["MISHE_NATIVE_RECOVERY_WAKE"] == str(wake)')
    (home / "minds" / ROLE).write_text(
        launcher_text(number, runner=runner_line(home, source)), encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "compares the opt-in with the pending wake at line" in result.stdout
