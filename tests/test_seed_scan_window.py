"""Discovery-scan reader: fail-closed over every scan file, freshness gate on the newest.

Decision (witness, 2026-11-01, after genome 149337): an old malformed scan must make
the aggregate unreadable, so every scan file is parsed. A window-only parse would
silently skip evidence; the cost is measured, not a reason to skip it.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mishe_tauftauf import seed


def _write_scan(d: Path, when: datetime, tag: str, obs=None, bad=False):
    name = f"scan-{when.strftime('%Y%m%dT%H%M%SZ')}-{tag}.json"
    body = "{not json" if bad else json.dumps({
        "created": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observations": [] if obs is None else obs,
    })
    (d / name).write_text(body, encoding="utf-8")


def _reference(home: Path):
    """Old behaviour: parse every scan file."""
    now = datetime.now(timezone.utc)
    scans = []
    for p in (home / "discovery").glob("scan-*.json"):
        s = json.loads(p.read_text(encoding="utf-8"))
        scans.append((datetime.fromisoformat(s["created"].replace("Z", "+00:00")), s))
    if not scans:
        return None, "no discovery scan"
    newest = max(c for c, _ in scans)
    latest = [s for c, s in scans if c == newest]
    if len(latest) != 1:
        return None, "newest scan timestamp is ambiguous"
    if (now - newest).total_seconds() > 1800:
        return None, "newest scan is stale"
    return latest[0]["observations"], None


@pytest.fixture
def home(tmp_path):
    d = tmp_path / "discovery"
    d.mkdir()
    return tmp_path


def test_matches_full_scan_with_many_old_scans(home):
    d = home / "discovery"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for i in range(2000):
        _write_scan(d, now - timedelta(hours=3, minutes=i), f"old{i}", bad=False)
    _write_scan(d, now - timedelta(minutes=5), "fresh", obs=[{"x": 1}])
    assert seed._newest_scan_observations(home) == _reference(home) == ([{"x": 1}], None)


def test_all_scans_outside_window_is_stale(home):
    d = home / "discovery"
    now = datetime.now(timezone.utc)
    _write_scan(d, now - timedelta(hours=4), "a", obs=[])
    assert seed._newest_scan_observations(home) == (None, "newest scan is stale")
    assert _reference(home) == (None, "newest scan is stale")


def test_no_scan_and_ambiguous_and_untimestamped_names(home):
    assert seed._newest_scan_observations(home) == (None, "no discovery scan")
    d = home / "discovery"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _write_scan(d, now - timedelta(minutes=2), "a", obs=[])
    _write_scan(d, now - timedelta(minutes=2), "b", obs=[])
    assert seed._newest_scan_observations(home) == (None, "newest scan timestamp is ambiguous")
    (d / "scan-1.json").write_text(json.dumps({"created": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), "observations": [1]}))
    assert seed._newest_scan_observations(home)[1] == "newest scan timestamp is ambiguous"


def test_malformed_old_named_scan_keeps_whole_aggregate_unreadable(home):
    # Witness 129094: an old-named malformed file must not be silently skipped;
    # the full-scan reference reports the aggregate as unreadable.
    d = home / "discovery"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _write_scan(d, now - timedelta(minutes=1), "fresh", obs=[])
    (d / "scan-20000101T000000Z-old.json").write_text("{not json", encoding="utf-8")
    assert seed._newest_scan_observations(home)[1] is not None


def test_future_dated_old_named_scan_keeps_whole_aggregate_unreadable(home):
    d = home / "discovery"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _write_scan(d, now - timedelta(minutes=1), "fresh", obs=[])
    future = now + timedelta(hours=1)
    (d / "scan-20000101T000000Z-old.json").write_text(json.dumps({
        "created": future.strftime("%Y-%m-%dT%H:%M:%SZ"), "observations": []}), encoding="utf-8")
    assert seed._newest_scan_observations(home)[1] is not None
