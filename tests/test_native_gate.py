"""Readiness-gate and delegation-branch detector coverage.

The exercises run the real probe script against a stubbed ``tmux`` on PATH, so
the engine test, the dead-pane test and the busy-file test are observed through
the same subprocess boundary the site uses. The measured defect is pinned by
running the retired ``python3``-only gate beside the canonical one.
"""
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf.feed import Feed

STUB_CASES = {
    "python-idle": {"probe": "0 python", "command": "python", "dead": "0"},
    "python3-idle": {"probe": "0 python3", "command": "python3", "dead": "0"},
    "python-versioned": {"probe": "0 python3.12", "command": "python3.12", "dead": "0"},
    "node": {"probe": "0 node", "command": "node", "dead": "0"},
    "dead-python": {"probe": "1 python", "command": "python", "dead": "1"},
    "omp": {"probe": "0 omp", "command": "omp", "dead": "0"},
}

STUB = """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
case = os.environ["STUB_CASE"]
if args[:1] == ["show-option"]:
    print(os.environ["STUB_HOME"])
    sys.exit(0)
if args[:1] == ["display-message"]:
    fields = args[-1]
    if "pane_dead" in fields and "pane_current_command" in fields:
        print(%(cases)r[case]["probe"])
    elif "pane_current_command" in fields:
        print(%(cases)r[case]["command"])
    elif "pane_dead" in fields:
        print(%(cases)r[case]["dead"])
    else:
        sys.exit(1)
    sys.exit(0)
sys.exit(1)
""" % {"cases": STUB_CASES}

# The retired probe: one engine literal. Kept here so the measured failure —
# a live native pane running ``python`` read not-ready — cannot silently return.
RETIRED_GATE = '''#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT = 2


def tmux(*args):
    return subprocess.run(("tmux", *args), capture_output=True, text=True, timeout=TIMEOUT)


def main() -> int:
    pane = os.environ.get("MISHE_SEED_PANE", "")
    role = os.environ.get("MISHE_SEED_ROLE", "")
    session = os.environ.get("MISHE_SEED_SESSION", "")
    if not pane or not role or not session:
        return 1
    owned = tmux("show-option", "-qv", "-t", session, "@mishe-tauftauf-home")
    if owned.returncode != 0 or not owned.stdout.strip():
        return 1
    home = Path(owned.stdout.strip())
    command = tmux("display-message", "-p", "-t", pane, "#{pane_current_command}")
    if command.returncode != 0 or command.stdout.strip() != "python3":
        return 1
    dead = tmux("display-message", "-p", "-t", pane, "#{pane_dead}")
    if dead.returncode != 0 or dead.stdout.strip() == "1":
        return 1
    if (home / "journals" / f"{role}-native.busy").exists():
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def make_home(tmp_path):
    (tmp_path / "minds").mkdir()
    (tmp_path / "top-pains").mkdir()
    (tmp_path / "journals").mkdir()
    return tmp_path


def run_stub(tmp_path, monkeypatch, case):
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "tmux"
    stub.write_text(STUB, encoding="utf-8")
    os.chmod(stub, 0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_CASE", case)
    monkeypatch.setenv("STUB_HOME", str(tmp_path))


def run_gate(gate: Path, tmp_path, monkeypatch, case, role="health"):
    run_stub(tmp_path, monkeypatch, case)
    env = {**os.environ, "MISHE_SEED_SESSION": "s", "MISHE_SEED_ROLE": role,
           "MISHE_SEED_PANE": f"s:{role}.1"}
    return subprocess.run([sys.executable, str(gate)], env=env, capture_output=True, text=True, timeout=10)


def append_wake(home, role="genome", body="seed wake {role} observation=1\nOwed work.\n"):
    return Feed(home).append("seed", body.format(role=role))


def test_retired_gate_rejected_a_live_python_pane(tmp_path, monkeypatch):
    """The measured defect: the python3-only literal held a live native mind."""
    home = make_home(tmp_path)
    retired = home / "retired-gate"
    retired.write_text(RETIRED_GATE, encoding="utf-8")
    os.chmod(retired, 0o755)
    assert run_gate(retired, home, monkeypatch, "python-idle").returncode == 1
    assert run_gate(retired, home, monkeypatch, "python3-idle").returncode == 0












































