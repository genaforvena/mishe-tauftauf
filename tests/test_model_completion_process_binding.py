"""Provider completion for a superseding process is read from the process's own open session file.

Controlled failure: model-change 97892f36 stayed `activating` because the canonical steward launcher
never writes `--session-dir`, so `completion` searched `<role>/sessions`, found nothing, and reported
`activated` forever. The live OMP process holds its session `.jsonl` open, so the binding is the
process itself, not a directory the launcher was expected to create.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

from mishe_tauftauf import model_boundary as boundary

TARGET = "opencode-go/muse-spark-1.2-contributor"


def _write_turn(path, stamp_ms, stop="stop"):
    row = {"type": "message", "message": {
        "role": "assistant", "provider": "opencode-go", "model": "muse-spark-1.2-contributor",
        "timestamp": stamp_ms, "stopReason": stop}}
    path.write_text(json.dumps(row) + "\n")


def _holder(path):
    """A live process holding the session file open, as the OMP mind does."""
    code = "import sys, time; f = open(sys.argv[1]); time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", code, str(path)])
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            if any(str(path) == _fd_target(child.pid, fd) for fd in _fds(child.pid)):
                return child
        except OSError:
            pass
        time.sleep(0.05)
    child.kill()
    raise AssertionError("holder did not open the session file")


def _fds(pid):
    import os
    return os.listdir(f"/proc/{pid}/fd")


def _fd_target(pid, fd):
    import os
    try:
        return os.readlink(f"/proc/{pid}/fd/{fd}")
    except OSError:
        return None


def test_completed_turn_in_live_process_session_is_verified(tmp_path):
    session = tmp_path / "omp" / "steward-session.jsonl"
    session.parent.mkdir()
    started = time.time() - 1
    _write_turn(session, int((started + 2) * 1000))
    # Canonical launcher: the role root has no sessions/ tree to search.
    directory = tmp_path / "root" / "steward"
    directory.mkdir(parents=True)
    child = _holder(session)
    try:
        assert boundary.completion(directory, TARGET, started, pid=child.pid) == "verified"
    finally:
        child.kill()
        child.wait()


def test_session_not_held_by_the_process_is_not_evidence(tmp_path):
    session = tmp_path / "omp" / "other-session.jsonl"
    session.parent.mkdir()
    started = time.time() - 1
    _write_turn(session, int((started + 2) * 1000))
    directory = tmp_path / "root" / "steward"
    directory.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert boundary.completion(directory, TARGET, started, pid=child.pid) == "activated"
    finally:
        child.kill()
        child.wait()
