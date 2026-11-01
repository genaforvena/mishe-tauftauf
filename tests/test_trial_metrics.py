from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import subprocess

from mishe_tauftauf import timestamp_text, trial_metrics, wall


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
    evidence_dir = home / "artifacts"
    evidence_dir.mkdir()
    evidence = evidence_dir / "evidence.txt"
    evidence.write_text("bounded controlled comparison")
    start = datetime.now(timezone.utc)
    wall.outcome(home, "discover", "hypothesis-changed", "Changed the next experiment.", evidence)
    sample = trial_metrics.collect(home, start)
    assert sample["outcomes"]["reported"]["hypothesis-changed"] == 1
    assert sample["outcomes"]["evidence_unavailable"] == 0
    assert sample["model_cost"]["state"] == "UNKNOWN"
    assert trial_metrics.collect(home, start + timedelta(hours=1))["outcomes"]["reported"] == {}
    evidence.chmod(0o600)
    evidence.write_text("changed evidence")
    sample = trial_metrics.collect(home, start)
    assert sample["outcomes"]["reported"] == {}
    assert sample["outcomes"]["evidence_unavailable"] == 1


def test_metrics_do_not_count_merely_applied_patches_as_verified_delivery(tmp_path, monkeypatch):
    home = site(tmp_path, monkeypatch)
    patches = home / "patches"
    patches.mkdir()
    (patches / "missing-revert.json").write_text(json.dumps({"phase": "applied"}))
    # A boolean without the verification binding is not verified delivery, the
    # same predicate wall_view renders.
    (patches / "flag-only.json").write_text(json.dumps({"phase": "applied", "delivery_verified": True}))
    (patches / "verified.json").write_text(json.dumps({
        "phase": "applied", "delivery_verified": True, "verification": {"at": datetime.now(timezone.utc).isoformat()}}))
    sample = trial_metrics.collect(home, datetime.now(timezone.utc) - timedelta(hours=1))
    assert sample["deliveries"]["verified_in_window"] == ["verified"]
    assert sorted(sample["deliveries"]["applied_without_verification"]) == ["flag-only", "missing-revert"]


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


class _Py310Datetime(datetime):
    """``datetime.fromisoformat`` under Python 3.10's accepted formats.

    Python 3.11 added the trailing "Z" to the formats ``fromisoformat``
    accepts; 3.10 rejects it, and the coordination-mode writer stamps its
    start with it.
    """

    @classmethod
    def fromisoformat(cls, value):
        if isinstance(value, str) and value.endswith("Z"):
            raise ValueError("invalid isoformat string on Python 3.10: " + value)
        return super().fromisoformat(value)


def test_the_window_reads_the_coordination_mode_stamp_on_python310(monkeypatch):
    """The sampler's window must decode the writer's own stamp on 3.10."""
    monkeypatch.setattr(trial_metrics, "datetime", _Py310Datetime)
    monkeypatch.setattr(timestamp_text, "datetime", _Py310Datetime)
    start, until = trial_metrics._window({
        "started": "2026-10-01T15:33:48.126048Z", "until": None})
    assert start == datetime(2026, 10, 1, 15, 33, 48, 126048, tzinfo=timezone.utc)
    assert until is None
    start, until = trial_metrics._window({
        "started": "2026-10-01T15:33:48.126048Z", "until": "2026-10-02T00:00:00Z"})
    assert start == datetime(2026, 10, 1, 15, 33, 48, 126048, tzinfo=timezone.utc)
    assert until == datetime(2026, 10, 2, tzinfo=timezone.utc)


class _Entry:
    def __init__(self, at, body):
        self.timestamp = at.isoformat().replace("+00:00", "Z")
        self.body = body
        self.source = "test"
        self.sequence = 0


def _placebo_feed(trial_start, per_window, body="seed observation x"):
    """Entries per placebo window k (window k spans [start-k*10h, start-(k-1)*10h))."""
    entries = []
    for k, n in per_window.items():
        hi = trial_start - timedelta(hours=10) * (k - 1)
        lo = trial_start - timedelta(hours=10) * k
        for i in range(n):
            entries.append(_Entry(lo + (hi - lo) * (i + 1) / (n + 1), body))
    return entries


def test_placebo_excludes_trial_baseline_and_outage_windows(tmp_path):
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    entries = _placebo_feed(start, {2: 2000, 3: 2500, 4: 10, 5: 3000, 11: 5000})
    entries += _placebo_feed(start + timedelta(hours=5), {0: 9000}) + _placebo_feed(start, {1: 9000})
    scale = trial_metrics.placebo_scale(entries, start)
    assert scale["kept"] == [2, 3, 5, 11] and scale["excluded"] == [4, 6, 7, 8, 9, 10]
    assert scale["entries"]["4"] == 10 and 0 not in scale["windows"] and 1 not in scale["windows"]
    # kept windows 2,3,5,11 have observations 2000,2500,3000,5000 -> diffs 500,500,2000
    assert round(scale["mean_abs_swing"]["observations"], 2) == round((500 + 500 + 2000) / 3, 2)


def test_placebo_zero_swing_reads_unknown_not_a_number(tmp_path):
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    entries = _placebo_feed(start, {2: 2000, 3: 2000, 4: 2000})
    scale = trial_metrics.placebo_scale(entries, start)
    assert scale["mean_abs_swing"]["observations"] is None
    assert scale["mean_abs_swing"]["wakes"] is None
    lines = trial_metrics._placebo_lines({"counts": {name: 0 for name in trial_metrics.PLACEBO_COUNTS}, "placebo": scale},
                                          {"counts": {}})
    assert any(line.startswith("| observations |") and "UNKNOWN (zero scale)" in line for line in lines)


def test_report_keeps_original_table_and_adds_scaled_section(tmp_path):
    directory = tmp_path / "trial"
    directory.mkdir()
    baseline = {"counts": {"wakes": 1, "redeliveries": 1, "yields": 1, "observations": 1, "addressed_messages": 1},
                "post_check_reports": 0, "post_check_refused": 0}
    sample = {"hours": 10.0, "end": "2026-11-01T10:00:00+00:00",
              "counts": {"wakes": 3, "redeliveries": 1, "settlements": 1, "observations": 4, "addressed_messages": 1},
              "post_check_reports": 0, "post_check_refused": 0, "services": {},
              "outcomes": {"reported": {}, "evidence_unavailable": 0}, "deliveries": {"verified_in_window": [], "applied_without_verification": [], "latency_seconds": {}},
              "limits": "x",
              "placebo": {"floor": 1000, "windows": [2], "kept": [2], "excluded": [], "entries": {"2": 5000},
                          "mean_abs_swing": {"wakes": 2.0, "redeliveries": None, "settlements": 1.0, "observations": 1.5, "addressed_messages": 0.5}}}
    (directory / "baseline.json").write_text(json.dumps(baseline))
    trial_metrics.report(tmp_path, directory, sample, {"services": {}})
    text = (directory / "report.md").read_text()
    assert "| wakes | 1 | 3 |" in text and "| settlements | 1 | 1 |" in text
    assert "| wakes | 2 | 2.00 | 1.00 |" in text
    assert "| redeliveries | 0 | UNKNOWN | UNKNOWN (zero scale) |" in text
    assert "excluded as feed-coverage gaps: none" in text
