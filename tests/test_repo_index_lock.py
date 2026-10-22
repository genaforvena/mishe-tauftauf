"""Deterministic fixtures for ``sense.repo.git-index-lock``.

The reading names the one delivery hazard that is invisible while it lasts: a
``.git/index.lock`` left behind by a crashed Git process blocks every ``git
add`` and ``git commit`` in the shared checkout, and nothing else in the plant
reads it. The fixtures build a fake checkout and a fake ``/proc`` so the three
states that look alike from the outside -- no lock, a lock a live process
holds, and a lock no live process holds -- are told apart without touching the
shared checkout or the real process table.

These fixtures live in their own file because the shared
``tests/test_discovery.py`` carries other roles' uncommitted work.
"""

import os
from pathlib import Path


def _checkout(tmp_path: Path) -> Path:
    """A checkout root holding this site's own home and a Git directory."""
    checkout = tmp_path / "checkout"
    (checkout / ".mishe-tauftauf").mkdir(parents=True)
    (checkout / ".git").mkdir()
    return checkout


def _proc(tmp_path: Path) -> Path:
    """An empty fake process table."""
    proc = tmp_path / "proc"
    proc.mkdir()
    return proc


def _process(proc: Path, pid: str, comm: bytes) -> Path:
    """One fake process with the given command name."""
    directory = proc / pid
    directory.mkdir()
    (directory / "comm").write_bytes(comm)
    (directory / "fd").mkdir()
    return directory


def _holding(proc: Path, pid: str, lock: Path) -> None:
    """Make one fake process hold the lock file open."""
    directory = proc / pid
    (directory / "fd").mkdir(parents=True, exist_ok=True)
    os.symlink(lock, directory / "fd" / "3")


def test_absent_lock_reads_verified(tmp_path):
    """No lock means the checkout can commit, and the reading says so."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)

    reading = discovery._repo_git_index_lock(checkout / ".mishe-tauftauf",
                                             proc_root=_proc(tmp_path))

    assert reading["state"] == "verified"
    assert reading["lock"] is False
    assert reading["sample"] == "index-lock=absent"


def test_lock_held_by_a_live_process_is_not_stale(tmp_path):
    """A lock a live Git process holds is a commit in flight: it ends on its
    own, so calling it stale would send a mind to remove a live lock."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    lock = checkout / ".git" / "index.lock"
    lock.write_bytes(b"")
    proc = _proc(tmp_path)
    _process(proc, "101", b"git\n")
    _holding(proc, "101", lock)

    reading = discovery._repo_git_index_lock(checkout / ".mishe-tauftauf",
                                             now=1000.0, proc_root=proc)

    assert reading["state"] == "drift"
    assert reading["holders"] == 1
    assert reading["git_processes"] == 1
    assert reading["stale"] is False
    assert "stale=no" in reading["sample"]


def test_lock_no_live_process_holds_is_stale(tmp_path):
    """The crashed-writer case: the lock outlived the process that made it, no
    live process holds it, and it will not end on its own."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    lock = checkout / ".git" / "index.lock"
    lock.write_bytes(b"")
    os.utime(lock, (400.0, 400.0))
    proc = _proc(tmp_path)
    _process(proc, "202", b"python3\n")

    reading = discovery._repo_git_index_lock(checkout / ".mishe-tauftauf",
                                             now=1000.0, proc_root=proc)

    assert reading["state"] == "drift"
    assert reading["holders"] == 0
    assert reading["git_processes"] == 0
    assert reading["stale"] is True
    assert "stale=yes" in reading["sample"]


def test_a_fresh_unheld_lock_is_not_stale(tmp_path):
    """A lock that is only seconds old may belong to a commit that started a
    moment ago, so the stale verdict waits for the age floor."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    lock = checkout / ".git" / "index.lock"
    lock.write_bytes(b"")
    os.utime(lock, (999.0, 999.0))

    reading = discovery._repo_git_index_lock(checkout / ".mishe-tauftauf",
                                             now=1000.0, proc_root=_proc(tmp_path))

    assert reading["state"] == "drift"
    assert reading["stale"] is False


def test_a_live_git_process_elsewhere_does_not_mask_a_crashed_lock(tmp_path):
    """Another mind's routine Git work must not hide a crashed writer's lock.

    Nine minds share this checkout, so a live Git process somewhere on the host
    is the normal state of the machine. The holder count is the evidence that
    matches the failure, so a lock no visible process holds open stays stale
    even while Git is running elsewhere."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    lock = checkout / ".git" / "index.lock"
    lock.write_bytes(b"")
    os.utime(lock, (400.0, 400.0))
    proc = _proc(tmp_path)
    _process(proc, "303", b"git\n")

    reading = discovery._repo_git_index_lock(checkout / ".mishe-tauftauf",
                                             now=1000.0, proc_root=proc)

    assert reading["state"] == "drift"
    assert reading["holders"] == 0
    assert reading["git_processes"] == 1
    assert reading["stale"] is True
    assert "stale=yes" in reading["sample"]


def test_an_unreadable_process_table_is_not_zero(tmp_path):
    """A broken monitor must not read as a healthy checkout: an unreadable
    process table is carried as unknown, not as an empty count, and an unknown
    holder count leaves the stale verdict unknown rather than "not stale"."""
    from mishe_tauftauf import discovery

    checkout = _checkout(tmp_path)
    lock = checkout / ".git" / "index.lock"
    lock.write_bytes(b"")

    reading = discovery._repo_git_index_lock(checkout / ".mishe-tauftauf",
                                             now=1000.0, proc_root=tmp_path / "absent")

    assert reading["state"] == "drift"
    assert reading["holders"] == -1
    assert reading["git_processes"] == -1
    assert reading["stale"] is None
    assert "holder=unknown" in reading["sample"]
    assert "git=unknown" in reading["sample"]
    assert "stale=unknown" in reading["sample"]


def test_a_checkout_without_a_git_directory_reads_unknown(tmp_path):
    """Without a Git directory there is no lock to name, and the reading must
    not claim the checkout is clear."""
    from mishe_tauftauf import discovery

    checkout = tmp_path / "checkout"
    (checkout / ".mishe-tauftauf").mkdir(parents=True)

    reading = discovery._repo_git_index_lock(checkout / ".mishe-tauftauf",
                                             proc_root=_proc(tmp_path))

    assert reading["state"] == "unknown"
    assert reading["lock"] is None
    assert "reason=no-gitdir" in reading["sample"]
