import fcntl
import multiprocessing
import time

import pytest

from mishe_tauftauf import chat_protocol, site_lock, state_files


def _hold(path, ready, release):
    with site_lock.lock(path):
        ready.set()
        release.wait(5)


def test_lock_excludes_another_process(tmp_path):
    (tmp_path / "chat.log").write_text("")
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    other = multiprocessing.Process(target=_hold, args=(tmp_path, ready, release))
    other.start()
    assert ready.wait(5)
    with (tmp_path / ".seed.lock").open("a+") as probe:
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    release.set()
    other.join(5)


def test_write_text_atomic_is_idempotent_and_creates_parents(tmp_path):
    target = tmp_path / "a" / "b.md"
    state_files.write_text_atomic(target, "hello")
    first = target.stat().st_mtime_ns
    time.sleep(0.01)
    state_files.write_text_atomic(target, "hello")
    assert target.read_text() == "hello" and target.stat().st_mtime_ns == first
    state_files.write_text_atomic(target, "changed")
    assert target.read_text() == "changed"


def test_observation_receipt_lines_decode():
    match = chat_protocol.OBS_RE.fullmatch(chat_protocol.receipt_line("seed observation signals sha256=" + "a" * 64 + "\nextra"))
    assert match and match.group(1) == "signals"
    assert chat_protocol.OBS_RE.fullmatch("seed observation Bad Role") is None
