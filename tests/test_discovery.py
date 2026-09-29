from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from mishe_tauftauf.discovery import latest, scan
from mishe_tauftauf.feed import Feed


def test_scan_writes_real_bounded_local_evidence(tmp_path: Path) -> None:
    home = tmp_path / "site"
    artifact = scan(home)
    snapshot = json.loads(artifact.read_text())
    assert latest(home) == snapshot
    observed = {row["id"]: row for row in snapshot["observations"]}
    assert observed["sense.proc.loadavg"]["state"] in {"verified", "unknown"}
    assert observed["sense.input.keyboard-interrupt-count"]["kind"] == "counter"
    assert observed["command.git"]["state"] == "available"
    assert all("/dev/input" not in str(row["sample"]) for row in observed.values())
    report = Feed(home).entries()[-1].body
    assert report.startswith("[discovery] Read-only scan at ")
    assert "Verified readings:" in report
    assert "Unknown readings:" in report
    assert f"Full sample: {artifact.resolve()}." in report
    assert "Next: senses should" in report
    shown = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                            "discover", "show"], capture_output=True, text=True)
    assert shown.returncode == 0
    assert "sense.proc.loadavg" in shown.stdout


def test_unchanged_scan_updates_artifact_without_log_spam(tmp_path: Path) -> None:
    home = tmp_path / "site"
    first = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": [
        {"id": "sense.value", "state": "verified", "sample": 1, "kind": "read"},
        {"id": "sense.missing", "state": "unknown", "sample": "source unavailable", "kind": "read"},
    ]}
    second = {"created": "2026-01-01T00:00:01Z", "node": "node", "observations": [
        {"id": "sense.value", "state": "verified", "sample": 2, "kind": "read"},
        {"id": "sense.missing", "state": "unknown", "sample": "source unavailable", "kind": "read"},
    ]}
    with patch("mishe_tauftauf.discovery.sample", side_effect=[first, second]):
        scan(home)
        scan(home)
    assert len(Feed(home).entries()) == 1
    assert latest(home) == second
