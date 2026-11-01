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


def _local(root: Path, usec: int, slice_name="mishe-plants.slice") -> None:
    (root / slice_name / "cpu.stat.local").write_text(f"throttled_usec {usec}\n")


def test_inherited_throttle_is_local_delta_minus_own_delta():
    previous = {"at": 1000.0, "nr_periods": 600, "nr_throttled": 100, "throttled_usec": 1_000_000,
                "local_throttled_usec": 5_000_000}
    current = {"at": 1060.0, "nr_periods": 1200, "nr_throttled": 670, "throttled_usec": 1_000_000 + 60_000_000,
               "local_throttled_usec": 5_000_000 + 90_000_000}
    verdict = st.throttle_verdict(previous, current, 1060.0)
    assert verdict["inherited"] == {"state": "verified", "throttled_usec": 30_000_000,
                                    "throttled_usec_per_min": 30_000_000.0}


def test_missing_cpu_stat_local_is_unknown_not_zero():
    previous = {"at": 1000.0, "nr_periods": 600, "nr_throttled": 100, "throttled_usec": 1, "local_throttled_usec": None}
    current = {"at": 1060.0, "nr_periods": 1200, "nr_throttled": 110, "throttled_usec": 2, "local_throttled_usec": None}
    verdict = st.throttle_verdict(previous, current, 1060.0)
    assert verdict["state"] == "verified"
    assert verdict["inherited"]["state"] == "unknown"
    assert "unavailable" in verdict["inherited"]["reason"]


def test_local_equal_to_own_means_no_inherited_throttle():
    previous = {"at": 1000.0, "nr_periods": 600, "nr_throttled": 0, "throttled_usec": 0, "local_throttled_usec": 0}
    current = {"at": 1060.0, "nr_periods": 1200, "nr_throttled": 300, "throttled_usec": 90_000_000,
               "local_throttled_usec": 90_000_000}
    verdict = st.throttle_verdict(previous, current, 1060.0)
    assert verdict["inherited"] == {"state": "verified", "throttled_usec": 0, "throttled_usec_per_min": 0.0}


def test_read_carries_cpu_stat_local_into_inherited_field(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "cg"
    _cgroup(root, 600, 0, 0)
    _local(root, 0)
    st.read_slice_throttle(home, root, now=1000.0)
    _cgroup(root, 1200, 300, 90_000_000)
    _local(root, 150_000_000)
    readings = st.read_slice_throttle(home, root, now=1060.0)
    plants = next(r for r in readings if r["id"] == "sense.cgroup.mishe-plants.throttle")
    assert plants["throttle"]["inherited"]["throttled_usec"] == 60_000_000
    assert plants["sample"].startswith("mishe-plants.slice window=60.0s ratio=0.5")
    # Missing cpu.stat.local on the lifeline slice: the own verdict stands, inherited is UNKNOWN.
    _cgroup(root, 489, 0, 0, "mishe-lifeline.slice")
    st.read_slice_throttle(home, root, now=1000.0)
    _cgroup(root, 978, 160, 12_000_000, "mishe-lifeline.slice")
    readings = st.read_slice_throttle(home, root, now=1060.0)
    lifeline = next(r for r in readings if r["id"] == "sense.cgroup.mishe-lifeline.throttle")
    assert lifeline["throttle"]["inherited"]["state"] == "unknown"


def test_throttled_cpus_is_own_throttled_time_over_period_budget():
    # Field-review sample (2026-11-01, plants 3-CPU quota, 100 ms period): 600 periods,
    # 153861602 usec throttled -> about 2.564 CPU-equivalents, not the 0.475 event ratio.
    previous = {"at": 1000.0, "nr_periods": 871811, "nr_throttled": 708921,
                "throttled_usec": 446053121711, "period_us": 100000}
    current = {"at": 1060.0, "nr_periods": 872411, "nr_throttled": 709206,
               "throttled_usec": 446206983313, "period_us": 100000}
    verdict = st.throttle_verdict(previous, current, 1060.0)
    assert verdict["ratio"] == 0.475
    assert verdict["throttled_cpus"] == {"state": "verified", "cpus": 2.564}


def test_throttled_cpus_unknown_without_cpu_max_or_on_period_change():
    base = {"at": 1000.0, "nr_periods": 600, "nr_throttled": 0, "throttled_usec": 0, "period_us": 100000}
    later = {"at": 1060.0, "nr_periods": 1200, "nr_throttled": 300, "throttled_usec": 90_000_000}
    no_max = st.throttle_verdict({**base}, {**later, "period_us": None}, 1060.0)
    assert no_max["throttled_cpus"]["state"] == "unknown"
    assert "unavailable or changed" in no_max["throttled_cpus"]["reason"]
    changed = st.throttle_verdict({**base}, {**later, "period_us": 50000}, 1060.0)
    assert changed["throttled_cpus"]["state"] == "unknown"


def test_parse_cpu_max_period():
    assert st.parse_cpu_max_period("300000 100000\n") == 100000
    assert st.parse_cpu_max_period("max 100000\n") == 100000
    assert st.parse_cpu_max_period("100000\n") is None
    assert st.parse_cpu_max_period("300000 0\n") is None
    assert st.parse_cpu_max_period(None) is None


def test_read_carries_cpu_max_period_into_throttled_cpus(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "cg"
    _cgroup(root, 600, 0, 0)
    (root / "mishe-plants.slice" / "cpu.max").write_text("300000 100000\n")
    st.read_slice_throttle(home, root, now=1000.0)
    _cgroup(root, 1200, 300, 150_000_000)
    readings = st.read_slice_throttle(home, root, now=1060.0)
    plants = next(r for r in readings if r["id"] == "sense.cgroup.mishe-plants.throttle")
    assert plants["throttle"]["throttled_cpus"] == {"state": "verified", "cpus": 2.5}
    assert plants["sample"].startswith("mishe-plants.slice window=60.0s ratio=0.5")
    # cpu.max absent on the next read: the baseline holds no period, so the figure is UNKNOWN.
    (root / "mishe-plants.slice" / "cpu.max").unlink()
    _cgroup(root, 1800, 300, 150_000_000)
    readings = st.read_slice_throttle(home, root, now=1120.0)
    plants = next(r for r in readings if r["id"] == "sense.cgroup.mishe-plants.throttle")
    assert plants["throttle"]["throttled_cpus"]["state"] == "unknown"
