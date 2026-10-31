import fcntl
import threading
import time

from mishe_tauftauf import recovery_io as recovery


def _lock_file(tmp_path):
    (tmp_path / ".seed.lock").touch()
    return (tmp_path / ".seed.lock").open("r+")


def test_probe_fails_when_holder_keeps_lock_past_bound(tmp_path):
    handle = _lock_file(tmp_path)
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        started = time.monotonic()
        result = recovery.seed_lock_probe(tmp_path, wait=0.3)
        elapsed = time.monotonic() - started
    finally:
        handle.close()
    assert result["ok"] is False
    assert "busy" in result["reason"]
    assert 0.25 <= elapsed < 2


def test_probe_passes_when_short_holder_releases_within_bound(tmp_path):
    handle = _lock_file(tmp_path)
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release_soon():
        time.sleep(0.5)
        handle.close()

    thread = threading.Thread(target=release_soon)
    thread.start()
    try:
        result = recovery.seed_lock_probe(tmp_path, wait=3)
    finally:
        thread.join()
    assert result["ok"] is True
    assert result["seconds"] < 3


def test_probe_passes_on_free_lock_without_waiting(tmp_path):
    _lock_file(tmp_path).close()
    started = time.monotonic()
    result = recovery.seed_lock_probe(tmp_path, wait=3)
    assert result["ok"] is True
    assert time.monotonic() - started < 1


def test_probe_default_bound_is_module_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "SEED_LOCK_PROBE_WAIT", 0.2)
    handle = _lock_file(tmp_path)
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = recovery.seed_lock_probe(tmp_path)
    finally:
        handle.close()
    assert result["ok"] is False
    assert "0.2s" in result["reason"]


def test_probe_rejects_negative_bound(tmp_path):
    _lock_file(tmp_path).close()
    try:
        recovery.seed_lock_probe(tmp_path, wait=-1)
    except ValueError:
        return
    raise AssertionError("negative bound must be rejected")


def test_probe_fails_when_holder_releases_after_bound(tmp_path):
    handle = _lock_file(tmp_path)
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release_after_bound():
        time.sleep(0.35)
        handle.close()

    thread = threading.Thread(target=release_after_bound)
    thread.start()
    try:
        result = recovery.seed_lock_probe(tmp_path, wait=0.2)
    finally:
        thread.join()
        handle.close()
    assert result["ok"] is False
    assert "busy" in result["reason"]


def test_probe_fails_on_late_wake_even_when_lock_is_free(tmp_path, monkeypatch):
    handle = _lock_file(tmp_path)
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    real_sleep = time.sleep

    def late_wake(seconds):
        # Simulate a scheduler pause: the holder goes away during the sleep
        # and the probe wakes only after the bound has passed.
        handle.close()
        real_sleep(0.5)

    monkeypatch.setattr(recovery.time, "sleep", late_wake)
    result = recovery.seed_lock_probe(tmp_path, wait=0.2)
    assert result["ok"] is False
    assert result["seconds"] >= 0.2
