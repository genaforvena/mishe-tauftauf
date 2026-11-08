"""A timed-out engine must not leave descendants alive, including new-session ones."""
import os
import time
from pathlib import Path

import pytest

from mishe_tauftauf.engine_process import run_bounded
from mishe_tauftauf.wake import EngineTimeout


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            return handle.read().decode("ascii", "replace").rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


@pytest.mark.parametrize("spawn", ["sleep 600 &", "setsid sleep 600 &"],
                         ids=["same-group", "new-session"])
def test_timeout_reaps_grandchildren(tmp_path: Path, spawn: str):
    pidfile = tmp_path / "child.pid"
    script = f"{spawn} echo $! > {pidfile}; wait"
    with pytest.raises(EngineTimeout):
        run_bounded(["/bin/sh", "-c", script], env=dict(os.environ), cwd=tmp_path, timeout=2)
    deadline = time.monotonic() + 5
    pid = int(pidfile.read_text().strip())
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid), f"grandchild {pid} survived the timeout kill ({spawn})"
