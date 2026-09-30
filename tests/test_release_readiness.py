from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mishe_tauftauf.release_readiness import report


def test_readiness_requires_clean_core_fresh_ci_and_all_linked_site_shas(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(workspace), "-c", "user.name=test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-qm", "candidate"], check=True)
    candidate = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"], check=True,
                               capture_output=True, text=True).stdout.strip()
    home = workspace / ".site"
    (workspace / ".git/info/exclude").write_text("/.site/\n")
    (home / "ci").mkdir(parents=True)
    (home / "health").mkdir()

    def store(path: Path, value: object) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    store(home / "ci/latest.json", {"state": "pass", "sha": candidate,
                                   "checked": datetime.now(timezone.utc).isoformat()})
    store(home / "health/linked-sites.json",
          {"version": 1, "sites": [{"sha": candidate, "error": ""}]})
    assert report(home) == {"state": "READY", "candidate_sha": candidate, "reasons": []}

    store(home / "health/linked-sites.json",
          {"version": 1, "sites": [{"sha": "", "error": "sync failed"}]})
    result = report(home)
    assert result["state"] == "HOLD"
    assert any("missing applied SHA" in reason for reason in result["reasons"])

    store(home / "health/linked-sites.json", {"version": 1, "sites": [{"sha": candidate}]})
    store(home / "ci/latest.json", {"state": "pass", "sha": candidate,
                                   "checked": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()})
    assert any("stale" in reason for reason in report(home)["reasons"])
    store(home / "ci/latest.json", {"state": "pass", "sha": candidate,
                                   "checked": datetime.now(timezone.utc).isoformat()})
    (workspace / "unlanded.py").touch()
    assert any("unlanded" in reason for reason in report(home)["reasons"])


def test_malformed_evidence_holds_instead_of_crashing(tmp_path):
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "ci").mkdir()
    (home / "ci/latest.json").write_text("[]")
    (home / "health/linked-sites.json").write_text("null")
    assert report(home)["state"] == "HOLD"
