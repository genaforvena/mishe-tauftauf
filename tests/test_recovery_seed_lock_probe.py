"""Debounced seed-lock availability probe.

A wake holds the seed lock for the length of a turn. A single non-blocking sample therefore
reads "busy" during healthy work. The probe must pass when a turn releases within its window and
fail only when the lock stays held for the whole window.
"""

import fcntl
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
    assert row["reason"] == "seed lock held for 1.0s window; availability not measured"
    assert 1.0 <= elapsed < 5.0


def test_missing_lock_file_is_unknown_not_busy(tmp_path: Path) -> None:
    row = seed_lock_probe(tmp_path, window=0.5)
    assert row["ok"] is None


def test_live_stamped_holder_within_bound_passes_stability(tmp_path: Path) -> None:
    # A turn that stamped its acquisition recently is live: busy for the whole window passes.
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 30)
    try:
        (tmp_path / ".seed.lock").write_text('{"acquired_at": %f}\n' % time.time())
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
        (tmp_path / ".seed.lock").write_text('{"acquired_at": %f}\n' % (time.time() - 500.0))
        row = seed_lock_probe(tmp_path, window=0.5, stuck_after=120.0)
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is False
    assert row["holder_age"] > 120.0
    assert "stuck holder suspected" in row["reason"]


def test_stamp_round_trip_is_read_and_cleared(tmp_path: Path) -> None:
    from mishe_tauftauf.recovery_io import clear_seed_holder, stamp_seed_holder
    (tmp_path / ".seed.lock").write_text("")
    with (tmp_path / ".seed.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # a real holder takes the lock before stamping
        stamp_seed_holder(handle, now=1_000.0)
        assert (tmp_path / ".seed.lock").read_text().split("\n")[0].startswith("{")
        row_live = seed_lock_probe(tmp_path, window=0.0, stuck_after=120.0)
        clear_seed_holder(handle)
    assert (tmp_path / ".seed.lock").read_text() == ""
    # This handle holds the flock; the stamp from time 1000 is far past the bound, so the holder is stuck.
    assert row_live["ok"] is False
