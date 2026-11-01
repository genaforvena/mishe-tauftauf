"""Debounced seed-lock availability probe.

A wake holds the seed lock for the length of a turn. A single non-blocking sample therefore
reads "busy" during healthy work. The probe must pass when a turn releases within its window and
fail only when the lock stays held for the whole window.
"""

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

from mishe_tauftauf.recovery_io import seed_lock_probe


def _hold(home: Path, seconds: float) -> subprocess.Popen:
    """Hold the seed lock in a separate process for the given time."""
    code = ("import fcntl,sys,time\n"
            "f=open(sys.argv[1],'a+')\n"
            "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "time.sleep(float(sys.argv[2]))\n")
    proc = subprocess.Popen([sys.executable, "-c", code, str(home / ".seed.lock"), str(seconds)],
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_free_lock_passes_immediately(tmp_path: Path) -> None:
    (tmp_path / ".seed.lock").write_text("")
    row = seed_lock_probe(tmp_path, window=2.0)
    assert row["ok"] is True
    assert row["seconds"] < 1.0


def test_lock_released_within_window_passes(tmp_path: Path) -> None:
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 0.5)
    try:
        row = seed_lock_probe(tmp_path, window=3.0)
    finally:
        holder.wait(timeout=10)
    assert row["ok"] is True


def test_lock_held_for_whole_window_is_unknown_not_failed_or_passed(tmp_path: Path) -> None:
    # Controlled failure path: a held lock must never read as ok. It is UNKNOWN (contention is not
    # a claimed failure of the probe, and the probe cannot tell a live turn from a stuck holder).
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 30)
    try:
        started = time.monotonic()
        row = seed_lock_probe(tmp_path, window=1.0)
        elapsed = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is None
    assert row["reason"].startswith("seed lock held for 1.0s window; availability not measured: no readable stamp; HELD, holder pid ")
    assert "stamp: no readable stamp" in row["reason"] and "PYTHONPATH=" in row["reason"]
    assert row["ok"] is None
    assert 1.0 <= elapsed < 5.0


def test_missing_lock_file_is_unknown_not_busy(tmp_path: Path) -> None:
    row = seed_lock_probe(tmp_path, window=0.5)
    assert row["ok"] is None


def test_live_stamped_holder_within_bound_passes_stability(tmp_path: Path) -> None:
    # A turn that stamped its acquisition recently is live: busy for the whole window passes.
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 30)
    try:
        (tmp_path / ".seed.lock").write_text('{"pid": %d, "acquired_at": %f}\n' % (holder.pid, time.time()))
        row = seed_lock_probe(tmp_path, window=0.5, stuck_after=120.0)
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is True
    assert 0.0 <= row["holder_age"] < 120.0


def test_stuck_stamped_holder_over_bound_fails_not_unknown(tmp_path: Path) -> None:
    # Controlled failure: a holder that stamped acquisition longer ago than the bound is stuck.
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 30)
    try:
        (tmp_path / ".seed.lock").write_text('{"pid": %d, "acquired_at": %f}\n' % (holder.pid, time.time() - 500.0))
        row = seed_lock_probe(tmp_path, window=0.5, stuck_after=120.0)
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is False
    assert row["holder_age"] > 120.0
    assert "stuck holder suspected" in row["reason"]


def test_stale_stamp_of_current_holder_over_bound_reads_stuck(tmp_path: Path) -> None:
    # Round trip through the real stamp helpers: the stamp names this process, which holds the flock,
    # so its age is judged. The stamp from time 1000 is far past the bound, so the holder is stuck.
    from mishe_tauftauf.recovery_io import clear_seed_holder, stamp_seed_holder
    (tmp_path / ".seed.lock").write_text("")
    with (tmp_path / ".seed.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # a real holder takes the lock before stamping
        stamp_seed_holder(handle, now=1_000.0)
        assert (tmp_path / ".seed.lock").read_text().split("\n")[0].startswith("{")
        row_live = seed_lock_probe(tmp_path, window=0.0, stuck_after=120.0)
        clear_seed_holder(handle)
    assert (tmp_path / ".seed.lock").read_text() == ""
    assert row_live["ok"] is False
    assert "stuck holder suspected" in row_live["reason"]


def test_stale_stamp_from_dead_holder_does_not_judge_live_unstamped_holder(tmp_path: Path) -> None:
    # The 557c74df defect: a dead holder's stamp (5 s old) sits in the file while a live holder that
    # never stamped holds the lock. The stamp is not the holder, so the probe must say UNKNOWN.
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 30)
    try:
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=10)
        (tmp_path / ".seed.lock").write_text('{"pid": %d, "acquired_at": %f}\n' % (dead.pid, time.time() - 5.0))
        row = seed_lock_probe(tmp_path, window=0.5, stuck_after=120.0)
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is None
    assert "is not the flock holder" in row["reason"]


def test_stamp_naming_a_different_live_pid_is_unknown(tmp_path: Path) -> None:
    # The stamp names a live process (this test runner) that does not hold the lock.
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 30)
    try:
        (tmp_path / ".seed.lock").write_text('{"pid": %d, "acquired_at": %f}\n' % (os.getpid(), time.time()))
        row = seed_lock_probe(tmp_path, window=0.5, stuck_after=120.0)
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is None
    assert "is not the flock holder" in row["reason"]


def test_failed_clear_leaves_stale_stamp_and_new_unstamped_holder_is_unknown(tmp_path: Path) -> None:
    # Exception path: clear_seed_holder swallows its failure, so a stamp can outlive its holder.
    # A later unstamped holder must not inherit that stamp's age.
    from mishe_tauftauf.recovery_io import clear_seed_holder, stamp_seed_holder
    (tmp_path / ".seed.lock").write_text("")
    with (tmp_path / ".seed.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        stamp_seed_holder(handle, now=1_000.0)
    clear_seed_holder(handle)  # closed handle: the failure is swallowed, no exception escapes
    assert (tmp_path / ".seed.lock").read_text().startswith("{")
    holder = _hold(tmp_path, 30)
    try:
        row = seed_lock_probe(tmp_path, window=0.5, stuck_after=120.0)
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is None
    assert "is not the flock holder" in row["reason"]


def test_stamp_in_the_future_clock_step_is_unknown(tmp_path: Path) -> None:
    # Wall-clock step: a stamp later than now is not a measurement; it reads UNKNOWN, not a pass.
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 30)
    try:
        (tmp_path / ".seed.lock").write_text('{"pid": %d, "acquired_at": %f}\n' % (holder.pid, time.time() + 3600.0))
        row = seed_lock_probe(tmp_path, window=0.5, stuck_after=120.0)
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is None
    assert "in the future" in row["reason"]
