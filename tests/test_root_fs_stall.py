"""root-fs-stall: stalls on the root drive go RED; a quiet or missing probe is UNKNOWN, never OK."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("root_fs_stall", Path(__file__).parent.parent / "checks" / "root-fs-stall.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

NOW = 10_000.0


def rec(dir_="root", at=NOW - 5, event="minute", total=0.0, **kw):
    return {"dir": dir_, "at": at, "event": event, "total": total, **kw}


def test_fresh_and_quiet_is_ok():
    assert mod.evaluate([rec()], NOW)[0] == 0


def test_stall_in_window_is_red_and_names_worst():
    code, lines = mod.evaluate([rec(), rec(event="slow", at=NOW - 60, total=9.9, fsync=9.9)], NOW)
    assert code == 2 and "9.9" in lines[0]


def test_old_stall_outside_window_is_ignored():
    assert mod.evaluate([rec(), rec(event="slow", at=NOW - 5000, total=9.9)], NOW)[0] == 0


def test_old_stall_is_reported_as_recurrence_while_quiet():
    code, lines = mod.evaluate([rec(), rec(event="slow", at=NOW - 5000, total=9.9)], NOW)
    assert code == 0 and "recurrence: 1 stall" in lines[0] and "most recent 83 min ago" in lines[0]


def test_stall_older_than_a_day_is_not_recurrence():
    code, lines = mod.evaluate([rec(), rec(event="slow", at=NOW - 90000, total=9.9)], NOW)
    assert code == 0 and "recurrence" not in lines[0]


def test_short_slow_probe_is_not_a_stall():
    assert mod.evaluate([rec(), rec(event="slow", at=NOW - 30, total=1.2)], NOW)[0] == 0


def test_other_targets_do_not_count_and_do_not_vouch():
    assert mod.evaluate([rec("scratch", event="slow", total=9.9)], NOW)[0] == 3   # no root records at all
    assert mod.evaluate([rec(), rec("scratch", event="slow", at=NOW - 30, total=9.9)], NOW)[0] == 0


def test_stale_probe_is_unknown_not_ok():
    assert mod.evaluate([rec(at=NOW - 4000)], NOW)[0] == 3


def test_missing_or_garbled_log(tmp_path):
    assert mod.main(["--home", str(tmp_path)]) == 3
    (tmp_path / "operator").mkdir()
    (tmp_path / "operator" / "dual-probe.jsonl").write_text("not json\n")
    assert mod.main(["--home", str(tmp_path)]) == 3
