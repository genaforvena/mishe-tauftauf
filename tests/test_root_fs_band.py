"""root-fs-band: a rising sub-threshold band goes RED; a flat or missing band never does."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("root_fs_band", Path(__file__).parent.parent / "checks" / "root-fs-band.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
stall = mod.stall

NOW = 100_000.0
W = stall.RATE_WINDOW_S


def slow(at, total=1.5):
    return {"dir": "root", "at": at, "event": "slow", "total": total}


def minute(at=NOW - 1):
    return {"dir": "root", "at": at, "event": "minute", "total": 0.0}


def probes_in_window(i, n):
    """n distinct probe-seconds in 30-min window i (0 = most recent)."""
    base = NOW - W * i - 1
    return [slow(base - k) for k in range(n)]


def baseline_quiet():
    # 12 prior windows with 1 probe-second each, so the 6 h median is 1.
    out = []
    for i in range(1, stall.RATE_BASELINE_WINDOWS + 1):
        out += probes_in_window(i, 1)
    return out


def test_no_probes_in_the_last_hour_is_not_rising():
    code, lines = mod.evaluate([minute()], NOW)
    assert code == 0 and "not rising" in lines[0]


def test_rise_to_twelve_against_flat_median_is_red():
    records = [minute()] + baseline_quiet() + probes_in_window(0, 12)
    code, lines = mod.evaluate(records, NOW)
    assert code == 2 and "rising" in lines[0] and "12" in lines[0]


def test_five_against_median_one_is_red_at_the_floor():
    records = [minute()] + baseline_quiet() + probes_in_window(0, 5)
    assert mod.evaluate(records, NOW)[0] == 2


def test_four_is_below_the_floor_and_not_rising():
    records = [minute()] + baseline_quiet() + probes_in_window(0, 4)
    assert mod.evaluate(records, NOW)[0] == 0


def test_stale_log_is_undecidable_not_ok():
    records = [{"dir": "root", "at": NOW - 3600, "event": "minute", "total": 0.0}]
    assert mod.evaluate(records, NOW)[0] == 3


def test_no_root_records_is_undecidable():
    assert mod.evaluate([{"dir": "var-tmp", "at": NOW - 1, "event": "minute"}], NOW)[0] == 3
