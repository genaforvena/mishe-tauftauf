"""A stale source sample reads UNKNOWN even when the scan itself is recent."""
from mishe_tauftauf.seed_culture_views import _observation_reading

STALE_ITEM = {
    "id": "sense.body.redmi10.dense-baseline",
    "kind": "read",
    "state": "stale",
    "sample": "L=22lux dim break=513970s",
}


def test_stale_sample_is_unknown_under_recent_scan() -> None:
    state, line = _observation_reading(dict(STALE_ITEM), "recent")
    assert state == "unknown"
    assert line.startswith("UNKNOWN sense.body.redmi10.dense-baseline:")
    assert "source_state=stale" in line


def test_stale_sample_is_unknown_under_old_scan() -> None:
    state, line = _observation_reading(dict(STALE_ITEM), "stale")
    assert state == "unknown"
    assert line.startswith("UNKNOWN ")


def test_verified_sample_under_recent_scan_keeps_its_state() -> None:
    item = dict(STALE_ITEM, state="verified", sample="ok")
    state, line = _observation_reading(item, "recent")
    assert state == "verified"
    assert line.startswith("VERIFIED ")


def test_unavailable_stays_unavailable() -> None:
    item = dict(STALE_ITEM, state="unavailable")
    state, line = _observation_reading(item, "stale")
    assert state == "unavailable"
    assert line.startswith("UNAVAILABLE ")
