"""A scan without the throttle senses shows them UNKNOWN; a stale verified row is not current."""
from mishe_tauftauf.seed_culture_views import _observation_reading
from mishe_tauftauf.slice_throttle import missing_throttle_rows

VERIFIED = {"id": "sense.cgroup.mishe-plants.throttle", "kind": "read", "state": "verified",
            "sample": "mishe-plants.slice window=60.0s ratio=0.1"}


def test_absent_row_renders_unknown_under_recent_scan():
    row = missing_throttle_rows([])[0]
    state, line = _observation_reading(row, "recent")
    assert state == "unknown"
    assert line.startswith("UNKNOWN sense.cgroup.mishe-plants.throttle:")


def test_verified_row_under_stale_scan_is_unknown():
    state, line = _observation_reading(dict(VERIFIED), "stale")
    assert state == "unknown"
    assert line.startswith("UNKNOWN ")


def test_verified_row_under_recent_scan_keeps_state():
    state, _ = _observation_reading(dict(VERIFIED), "recent")
    assert state == "verified"
