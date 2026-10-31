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


def test_lock_held_for_whole_window_fails(tmp_path: Path) -> None:
    (tmp_path / ".seed.lock").write_text("")
    holder = _hold(tmp_path, 30)
    try:
        started = time.monotonic()
        row = seed_lock_probe(tmp_path, window=1.0)
        elapsed = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert row["ok"] is False
    assert row["reason"] == "seed lock busy for 1.0s window"
    assert 1.0 <= elapsed < 5.0


def test_missing_lock_file_is_unknown_not_busy(tmp_path: Path) -> None:
    row = seed_lock_probe(tmp_path, window=0.5)
    assert row["ok"] is None
