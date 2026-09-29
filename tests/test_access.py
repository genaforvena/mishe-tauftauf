from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mishe_tauftauf.access import get, list_requests
from mishe_tauftauf.feed import Feed


def run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                           "access", *args], text=True, capture_output=True)


def test_request_records_scope_and_revocable_decision(tmp_path: Path) -> None:
    home = tmp_path / "site"
    request = ("request", "keyboard-counter", "--owner", "senses", "--task", "sense-keyboard",
               "--capability", "input.activity.count", "--unblocks", "senses/keyboard",
               "--unblocks", "task/sense-keyboard/verify", "--reason", "read event counts, not keys")
    created = run(home, *request)
    assert created.returncode == 0, created.stderr
    assert run(home, *request).returncode == 0
    assert len(list_requests(home)) == 1
    item = get(home, "keyboard-counter")
    assert item.unblocks == ("senses/keyboard", "task/sense-keyboard/verify")
    assert item.reason == "read event counts, not keys"
    assert run(home, "check", item.identity).returncode == 1
    assert "pending" in run(home, "list").stdout
    assert run(home, "grant", item.identity).returncode == 0
    assert run(home, "check", item.identity).returncode == 0
    assert run(home, "grant", item.identity).returncode == 0
    assert run(home, "revoke", item.identity).returncode == 0
    assert run(home, "check", item.identity).returncode == 1
    decisions = [entry.body for entry in Feed(home).entries() if entry.source == "operator/permissions"]
    assert len(decisions) == 2
    assert "decision=granted" in decisions[0]
    assert "decision=revoked" in decisions[1]


def test_request_id_and_scope_cannot_be_silently_reused(tmp_path: Path) -> None:
    home = tmp_path / "site"
    base = ("--owner", "discover", "--task", "frontier", "--capability", "proc.read",
            "--unblocks", "discover/proc", "--reason", "sample readable counters")
    assert run(home, "request", "../escape", *base).returncode == 2
    assert run(home, "request", "proc-sample", *base).returncode == 0
    changed = run(home, "request", "proc-sample", *base[:-1], "different reason")
    assert changed.returncode == 2
    assert "different scope" in changed.stderr
