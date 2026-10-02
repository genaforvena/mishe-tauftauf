from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import subprocess

from mishe_tauftauf import trial_metrics, wall


def site(tmp_path, monkeypatch):
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["test.service"]))
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall"}))
    monkeypatch.setattr(trial_metrics.subprocess, "run", lambda *a, **k:
        subprocess.CompletedProcess(a[0], 0, "ActiveState=active\nSubState=running\nNRestarts=0\n", ""))
    return home


def test_metrics_count_only_in_window_outcomes_with_intact_evidence(tmp_path, monkeypatch):
    home = site(tmp_path, monkeypatch)
    evidence = home / "evidence.txt"
    evidence.write_text("bounded controlled comparison")
    start = datetime.now(timezone.utc)
    wall.outcome(home, "discover", "hypothesis-changed", "Changed the next experiment.", evidence)
    sample = trial_metrics.collect(home, start)
    assert sample["outcomes"]["reported"]["hypothesis-changed"] == 1
    assert sample["outcomes"]["evidence_unavailable"] == 0
    assert sample["model_cost"]["state"] == "UNKNOWN"
    assert trial_metrics.collect(home, start + timedelta(hours=1))["outcomes"]["reported"] == {}
    evidence.write_text("changed evidence")
    sample = trial_metrics.collect(home, start)
    assert sample["outcomes"]["reported"] == {}
    assert sample["outcomes"]["evidence_unavailable"] == 1


def test_metrics_do_not_count_merely_applied_patches_as_verified_delivery(tmp_path, monkeypatch):
    home = site(tmp_path, monkeypatch)
    patches = home / "patches"
    patches.mkdir()
    (patches / "missing-revert.json").write_text(json.dumps({"phase": "applied"}))
    sample = trial_metrics.collect(home, datetime.now(timezone.utc) - timedelta(hours=1))
    assert sample["deliveries"]["verified_in_window"] == []
    assert sample["deliveries"]["applied_without_verification"] == ["missing-revert"]


def test_sampler_preserves_partial_sample_on_service_timeout(tmp_path, monkeypatch):
    home = site(tmp_path, monkeypatch)
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 5)
    monkeypatch.setattr(trial_metrics.subprocess, "run", timeout)
    sample = trial_metrics.collect(home, datetime.now(timezone.utc))
    assert "unknown" in sample["services"]["test.service"]


def test_sampler_accepts_open_ended_window_until_null(tmp_path, monkeypatch):
    home = site(tmp_path, monkeypatch)
    (home / "coordination-mode.json").write_text(json.dumps({
        "mode": "wall", "started": "2026-10-01T15:33:48.126048+00:00", "until": None}))
    directory = tmp_path / "metrics"
    monkeypatch.setattr("sys.argv", ["trial_metrics", "--home", str(home), "--directory", str(directory)])
    trial_metrics.main()
    assert (directory / "latest.json").exists()
    assert (directory / "report.md").exists()
