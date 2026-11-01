import json
from pathlib import Path

from mishe_tauftauf import slice_throttle as st

STAT = (
    "usage_usec 1\nuser_usec 1\nsystem_usec 0\nnr_periods {periods}\n"
    "nr_throttled {throttled}\nthrottled_usec {usec}\nnr_bursts 0\nburst_usec 0\n"
)


def _cgroup(root: Path, periods: int, throttled: int, usec: int, slice_name="mishe-plants.slice") -> None:
    directory = root / slice_name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "cpu.stat").write_text(STAT.format(periods=periods, throttled=throttled, usec=usec))


def test_parse_cpu_stat_requires_all_counters():
    assert st.parse_cpu_stat(STAT.format(periods=10, throttled=4, usec=99)) == {
        "nr_periods": 10, "nr_throttled": 4, "throttled_usec": 99}
    assert st.parse_cpu_stat("nr_periods 10\n") is None
    assert st.parse_cpu_stat(None) is None
    assert st.parse_cpu_stat(STAT.format(periods="x", throttled=1, usec=1)) is None


def test_per_minute_delta_matches_discover_sample_shape():
    previous = {"at": 1000.0, "nr_periods": 600, "nr_throttled": 100, "throttled_usec": 1_000_000}
    current = {"at": 1060.0, "nr_periods": 1200, "nr_throttled": 670, "throttled_usec": 332_800_000}
    verdict = st.throttle_verdict(previous, current, 1060.0)
    assert verdict["state"] == "verified"
    assert verdict["periods"] == 600
    assert verdict["throttled"] == 570
    assert verdict["ratio"] == 0.95
    assert verdict["periods_per_min"] == 600.0
    assert verdict["throttled_per_min"] == 570.0


def test_gap_bounds_and_counter_reset_are_unknown():
    base = {"at": 1000.0, "nr_periods": 10, "nr_throttled": 1, "throttled_usec": 1}
    later = {"nr_periods": 20, "nr_throttled": 2, "throttled_usec": 2}
    assert st.throttle_verdict(base, {**later, "at": 1005.0}, 1005.0)["state"] == "unknown"
    assert st.throttle_verdict(base, {**later, "at": 3000.0}, 3000.0)["state"] == "unknown"
    reset = {"at": 1060.0, "nr_periods": 5, "nr_throttled": 0, "throttled_usec": 0}
    assert "backwards" in st.throttle_verdict(base, reset, 1060.0)["reason"]
    flat = {"at": 1060.0, "nr_periods": 10, "nr_throttled": 1, "throttled_usec": 1}
    assert st.throttle_verdict(base, flat, 1060.0)["state"] == "unknown"


def test_read_persists_baseline_and_reports_both_slices(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "cg"
    _cgroup(root, 600, 100, 1_000_000)
    _cgroup(root, 489, 0, 0, "mishe-lifeline.slice")
    first = st.read_slice_throttle(home, root, now=1000.0)
    assert {reading["id"] for reading in first} == {
        "sense.cgroup.mishe-plants.throttle", "sense.cgroup.mishe-lifeline.throttle"}
    assert all(reading["state"] == "unknown" for reading in first)
    assert "no prior sample" in first[0]["sample"]

    _cgroup(root, 1200, 670, 332_800_000)
    _cgroup(root, 978, 160, 12_000_000, "mishe-lifeline.slice")
    second = st.read_slice_throttle(home, root, now=1060.0)
    plants = next(r for r in second if r["id"] == "sense.cgroup.mishe-plants.throttle")
    lifeline = next(r for r in second if r["id"] == "sense.cgroup.mishe-lifeline.throttle")
    assert plants["state"] == "verified"
    assert plants["throttle"]["ratio"] == 0.95
    assert lifeline["throttle"]["ratio"] == round(160 / 489, 4)
    state = json.loads((home / "discovery" / st.STATE_NAME).read_text())
    assert state["mishe-plants.slice"]["at"] == 1060.0


def test_unreadable_slice_is_unknown_and_drops_baseline(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "cg"
    _cgroup(root, 600, 100, 1_000_000)
    readings = st.read_slice_throttle(home, root, now=1000.0)
    lifeline = next(r for r in readings if "lifeline" in r["id"])
    assert lifeline["state"] == "unknown"
    assert "unreadable" in lifeline["sample"]
    state = json.loads((home / "discovery" / st.STATE_NAME).read_text())
    assert "mishe-lifeline.slice" not in state


def test_self_limit_throttle_is_verified_reading(tmp_path):
    # Counter scope (module docstring): the counters are the slice's own limit.
    # A slice whose own cpu.stat shows throttling reads as verified with that throttle.
    home = tmp_path / "home"
    root = tmp_path / "cg"
    _cgroup(root, 600, 0, 0)
    st.read_slice_throttle(home, root, now=1000.0)
    _cgroup(root, 1200, 300, 90_000_000)
    readings = st.read_slice_throttle(home, root, now=1060.0)
    plants = next(r for r in readings if r["id"] == "sense.cgroup.mishe-plants.throttle")
    assert plants["state"] == "verified"
    assert plants["throttle"]["throttled"] == 300
    assert plants["throttle"]["ratio"] == 0.5
