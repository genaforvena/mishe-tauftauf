from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from mishe_tauftauf import scan_freshness as freshness


_BOOT = "12345678-1234-1234-1234-123456789abc"
_NAMESPACE = "time:[4026531834]"


def _endpoint(first: int, last: int, utc: int = 1_000) -> dict:
    return {
        "clock": "CLOCK_BOOTTIME",
        "bounds_ns": [first, last],
        "boot_id": _BOOT,
        "time_namespace": _NAMESPACE,
        "offsets": {"monotonic": [-3, 999_999_999], "boottime": [0, 0]},
        "utc_ns": utc,
    }


def _snapshot() -> dict:
    return {
        "created": "2099-01-01T00:00:00Z",
        "acquisition": {"start": _endpoint(100, 110), "end": _endpoint(200, 210, 1_100)},
    }


def test_age_bounds_cover_full_acquisition_and_current_uncertainty() -> None:
    assert freshness.age_bounds(_snapshot(), _endpoint(300, 310)) == (90, 210)
    assert freshness.age_bounds(_snapshot(), _endpoint(210, 210)) == (0, 110)


@pytest.mark.parametrize("snapshot", [
    None, [], {}, {"created": "2099-01-01T00:00:00Z"},
    {"acquisition": None}, {"acquisition": {}},
    {"acquisition": {"start": _endpoint(100, 110)}},
    {"acquisition": {"end": _endpoint(200, 210)}},
    {"acquisition": {"start": None, "end": _endpoint(200, 210)}},
    {"acquisition": {"start": _endpoint(100, 110), "end": None}},
])
def test_missing_or_legacy_anchors_remain_unknown(snapshot) -> None:
    assert freshness.age_bounds(snapshot, _endpoint(300, 310)) is None
    assert freshness.utc_consistency(snapshot) == "unknown"


@pytest.mark.parametrize(("key", "value"), [
    ("clock", "CLOCK_MONOTONIC"),
    ("bounds_ns", [True, 110]),
    ("bounds_ns", [100, False]),
    ("bounds_ns", [100.0, 110]),
    ("bounds_ns", [-1, 110]),
    ("bounds_ns", [110, 100]),
    ("bounds_ns", [100]),
    ("bounds_ns", (100, 110)),
    ("boot_id", _BOOT.upper()),
    ("boot_id", "12345678123412341234123456789abc"),
    ("boot_id", None),
    ("time_namespace", "time:[04026531834]"),
    ("time_namespace", "time_for_children:[4026531834]"),
    ("time_namespace", "time:[-1]"),
    ("time_namespace", None),
    ("utc_ns", True),
    ("utc_ns", -1),
    ("utc_ns", 1_000.0),
    ("offsets", {"monotonic": [0, 0]}),
    ("offsets", {"monotonic": [0, 0], "boottime": [0, 0], "extra": [0, 0]}),
    ("offsets", {"monotonic": [True, 0], "boottime": [0, 0]}),
    ("offsets", {"monotonic": [0, True], "boottime": [0, 0]}),
    ("offsets", {"monotonic": [0, -1], "boottime": [0, 0]}),
    ("offsets", {"monotonic": [0, 1_000_000_000], "boottime": [0, 0]}),
    ("offsets", {"monotonic": [0.0, 0], "boottime": [0, 0]}),
    ("offsets", {"monotonic": [0, 0, 0], "boottime": [0, 0]}),
    ("offsets", {"monotonic": (0, 0), "boottime": [0, 0]}),
])
def test_malformed_endpoint_is_never_recency_evidence(key, value) -> None:
    for location in ("start", "end", "current"):
        snapshot = _snapshot()
        current = _endpoint(300, 310)
        target = current if location == "current" else snapshot["acquisition"][location]
        target[key] = deepcopy(value)
        assert freshness.age_bounds(snapshot, current) is None
        if location != "current":
            assert freshness.utc_consistency(snapshot) == "unknown"


@pytest.mark.parametrize("key", ["clock", "bounds_ns", "boot_id", "time_namespace", "offsets", "utc_ns"])
def test_incomplete_endpoint_cannot_supply_age_or_utc_evidence(key) -> None:
    snapshot = _snapshot()
    del snapshot["acquisition"]["start"][key]
    assert freshness.age_bounds(snapshot, _endpoint(300, 310)) is None
    assert freshness.utc_consistency(snapshot) == "unknown"


def test_unknown_endpoint_fields_do_not_silently_change_domain_contract() -> None:
    snapshot = _snapshot()
    snapshot["acquisition"]["start"]["other_clock"] = "CLOCK_MONOTONIC"
    assert freshness.age_bounds(snapshot, _endpoint(300, 310)) is None
    assert freshness.utc_consistency(snapshot) == "unknown"


@pytest.mark.parametrize(("key", "value"), [
    ("boot_id", "22345678-1234-1234-1234-123456789abc"),
    ("time_namespace", "time:[4026531835]"),
    ("offsets", {"monotonic": [-2, 999_999_999], "boottime": [0, 0]}),
    ("offsets", {"monotonic": [-3, 999_999_999], "boottime": [1, 0]}),
])
def test_each_domain_component_must_match_across_all_endpoints(key, value) -> None:
    for location in ("start", "end", "current"):
        snapshot = _snapshot()
        current = _endpoint(300, 310)
        target = current if location == "current" else snapshot["acquisition"][location]
        target[key] = deepcopy(value)
        assert freshness.age_bounds(snapshot, current) is None
        if location != "current":
            assert freshness.utc_consistency(snapshot) == "unknown"


@pytest.mark.parametrize("end_bounds", [[90, 95], [105, 210]])
def test_acquisition_time_reversal_or_overlapping_endpoint_reads_are_unknown(end_bounds) -> None:
    snapshot = _snapshot()
    snapshot["acquisition"]["end"]["bounds_ns"] = end_bounds
    assert freshness.age_bounds(snapshot, _endpoint(300, 310)) is None
    assert freshness.utc_consistency(snapshot) == "unknown"


def test_current_read_preceding_acquisition_end_is_unknown() -> None:
    assert freshness.age_bounds(_snapshot(), _endpoint(209, 310)) is None


def test_unavailable_current_clock_has_no_utc_fallback(monkeypatch) -> None:
    monkeypatch.setattr(freshness, "endpoint", lambda: None)
    assert freshness.age_bounds(_snapshot()) is None
    assert freshness.classify_age(None, 300) == "unknown"


@pytest.mark.parametrize(("bounds", "threshold", "expected"), [
    ((0, 0), 0, "recent"),
    ((0, 1), 0, "unknown"),
    ((1, 1), 0, "stale"),
    ((90, 210), 210e-9, "recent"),
    ((90, 210), 90e-9, "unknown"),
    ((90, 210), 89e-9, "stale"),
    ((300_000_000, 300_000_000), 0.3, "recent"),
    ((300_000_000, 300_000_001), 0.3, "unknown"),
    ((300_000_001, 300_000_001), 0.3, "stale"),
    ((1, 1), 1e-9, "recent"),
    ((1, 2), 1e-9, "unknown"),
    ((2, 2), 1e-9, "stale"),
    ((0, 1), 0.5e-9, "unknown"),
    ((1, 1), 0.5e-9, "stale"),
    (((2**60) * 1_000_000_000, (2**60) * 1_000_000_000), 2**60, "recent"),
    (((2**60) * 1_000_000_000 + 1, (2**60) * 1_000_000_000 + 1), 2**60, "stale"),
])
def test_classification_uses_inclusive_exact_nanosecond_boundaries(bounds, threshold, expected) -> None:
    assert freshness.classify_age(bounds, threshold) == expected


@pytest.mark.parametrize("bounds", [None, (), (1,), (2, 1), (-1, 0), (False, 1), (0, True), (0, 1.0)])
def test_invalid_age_bounds_are_unknown(bounds) -> None:
    assert freshness.classify_age(bounds, 300) == "unknown"


@pytest.mark.parametrize("threshold", [-1, True, None, "300", float("nan"), float("inf"), -float("inf")])
def test_invalid_threshold_is_unknown(threshold) -> None:
    assert freshness.classify_age((0, 1), threshold) == "unknown"


@pytest.mark.parametrize(("wall_delta", "expected"), [
    (90, "consistent"), (100, "consistent"), (110, "consistent"),
    (89, "anomaly"), (111, "anomaly"), (-1, "anomaly"),
])
def test_utc_delta_diagnostic_does_not_control_age(wall_delta, expected) -> None:
    snapshot = _snapshot()
    snapshot["acquisition"]["end"]["utc_ns"] = 1_000 + wall_delta
    assert freshness.utc_consistency(snapshot) == expected
    assert freshness.age_bounds(snapshot, _endpoint(300, 310, 0)) == (90, 210)


def _mock_endpoint_reads(monkeypatch, *, clocks=(300, 310), boot=_BOOT,
                         namespace=_NAMESPACE, offsets="monotonic -3 999999999\nboottime 0 0\n",
                         utc=1_200) -> None:
    values = iter(clocks)
    monkeypatch.setattr(freshness.time, "clock_gettime_ns", lambda clock: next(values))
    monkeypatch.setattr(freshness.time, "time_ns", lambda: utc)
    monkeypatch.setattr(freshness.os, "readlink", lambda path: namespace)

    def read_text(path: Path) -> str:
        if path == Path("/proc/sys/kernel/random/boot_id"):
            if isinstance(boot, Exception):
                raise boot
            return boot + "\n"
        return offsets

    monkeypatch.setattr(Path, "read_text", read_text)


def test_read_only_endpoint_provides_bounded_age_and_utc_evidence(monkeypatch) -> None:
    _mock_endpoint_reads(monkeypatch)
    current = freshness.endpoint()
    assert freshness.age_bounds(_snapshot(), current) == (90, 210)
    assert freshness.utc_consistency({"acquisition": {
        "start": _endpoint(100, 110), "end": current,
    }}) == "consistent"


@pytest.mark.parametrize("overrides", [
    {"clocks": (310, 300)}, {"clocks": (-1, 310)}, {"clocks": (True, 310)},
    {"boot": OSError("unavailable")}, {"boot": _BOOT.upper()},
    {"namespace": "time:[04026531834]"},
    {"offsets": "monotonic 0 0\n"},
    {"offsets": "monotonic 0 0\nmonotonic 0 0\nboottime 0 0\n"},
    {"offsets": "monotonic 0 0\nboottime 0 0\nother 0 0\n"},
    {"offsets": "monotonic 0 1000000000\nboottime 0 0\n"},
    {"offsets": "monotonic 0 -1\nboottime 0 0\n"},
    {"offsets": "monotonic invalid 0\nboottime 0 0\n"},
    {"utc": -1}, {"utc": True},
])
def test_unavailable_or_invalid_measured_endpoint_is_none(monkeypatch, overrides) -> None:
    _mock_endpoint_reads(monkeypatch, **overrides)
    assert freshness.endpoint() is None


def test_unavailable_boottime_clock_is_none(monkeypatch) -> None:
    def unavailable(clock):
        raise OSError("CLOCK_BOOTTIME unavailable")

    monkeypatch.setattr(freshness.time, "clock_gettime_ns", unavailable)
    assert freshness.endpoint() is None
