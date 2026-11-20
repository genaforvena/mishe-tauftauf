"""root-fs-stall: stalls on the root drive go RED; a quiet or missing probe is UNKNOWN, never OK."""
from __future__ import annotations

import importlib.util
import json
import time
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


def test_stall_line_carries_the_probe_io_pressure():
    code, lines = mod.evaluate(
        [rec(), rec(event="slow", at=NOW - 60, total=17.0, fsync=16.97, psi_io=84.66)], NOW)
    assert code == 2 and "io_psi 84.66" in lines[0]


def test_stall_line_omits_io_pressure_when_the_probe_did_not_record_it():
    code, lines = mod.evaluate(
        [rec(), rec(event="slow", at=NOW - 60, total=17.0, fsync=16.97)], NOW)
    assert code == 2 and "io_psi" not in lines[0]


def test_stall_line_carries_the_probe_memory_pressure_beside_io():
    code, lines = mod.evaluate(
        [rec(), rec(event="slow", at=NOW - 60, total=17.0, fsync=16.97, psi_io=84.66, psi_mem=23.93)], NOW)
    assert code == 2 and "io_psi 84.66" in lines[0] and "mem_psi 23.93" in lines[0]


def test_stall_line_renders_memory_pressure_alone_when_io_is_absent():
    code, lines = mod.evaluate(
        [rec(), rec(event="slow", at=NOW - 60, total=17.0, fsync=16.97, psi_mem=23.93)], NOW)
    assert code == 2 and "mem_psi 23.93" in lines[0] and "io_psi" not in lines[0]


def test_one_cliff_seen_by_three_probes_is_one_episode():
    stalls = [rec(event="slow", at=NOW - 60 + i * 0.01, total=5.2 + i, fsync=5.2 + i)
              for i in range(3)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2
    assert "1 root-filesystem stall episode(s)" in lines[0]
    assert "3 near-simultaneous probe reports" in lines[0]
    assert "worst 7.2s" in lines[0]


def test_distinct_stalls_are_not_collapsed_into_one_episode():
    stalls = [rec(event="slow", at=NOW - 60, total=9.9, fsync=9.9),
              rec(event="slow", at=NOW - 50, total=8.1, fsync=8.1)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2 and "2 root-filesystem stall(s)" in lines[0]
    assert "episode" not in lines[0]


def test_stall_line_reports_recency_and_burst_span():
    stalls = [rec(event="slow", at=NOW - 600, total=9.9, fsync=9.9),
              rec(event="slow", at=NOW - 120, total=8.1, fsync=8.1)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2
    assert "most recent stall 120s ago" in lines[0]
    assert "bursts span 8 min" in lines[0]


def test_single_episode_reports_recency_without_a_span():
    stalls = [rec(event="slow", at=NOW - 600 + i * 0.01, total=5.2, fsync=5.2) for i in range(3)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2
    assert "most recent stall 600s ago" in lines[0]
    assert "bursts span" not in lines[0]


def test_recurrence_counts_episodes_when_probes_overlap():
    stalls = [rec(event="slow", at=NOW - 5000 + i * 0.01, total=9.9, fsync=9.9) for i in range(3)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 0
    assert "recurrence: 1 stall episode(s) from 3 probe reports" in lines[0]
    assert "most recent 83 min ago" in lines[0]


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
    missing = tmp_path / "absent.jsonl"
    assert mod.main(["--home", str(tmp_path), "--live-log", str(missing)]) == 3
    (tmp_path / "operator").mkdir()
    (tmp_path / "operator" / "dual-probe.jsonl").write_text("not json\n")
    assert mod.main(["--home", str(tmp_path), "--live-log", str(missing)]) == 3


def test_load_unions_logs_and_the_later_file_wins(tmp_path):
    live = tmp_path / "live.jsonl"
    mirror = tmp_path / "mirror.jsonl"
    live.write_text(json.dumps(rec(event="slow", at=NOW - 60, total=9.9)) + "\n"
                    + json.dumps(rec(at=NOW, total=1.0)) + "\n")
    mirror.write_text("garbage\n" + json.dumps(rec(at=NOW, total=1.0)) + "\n")
    records = mod.load([live, mirror])
    assert len(records) == 2                                  # shared (at,dir,event) deduped, garbage skipped
    assert mod.evaluate(records, NOW)[0] == 2                 # the live-log-only stall is seen


def test_main_reads_the_live_log(tmp_path):
    live = tmp_path / "live.jsonl"
    now = time.time()
    live.write_text(json.dumps({"dir": "root", "event": "slow", "at": now - 60, "total": 9.9}) + "\n"
                    + json.dumps({"dir": "root", "event": "minute", "at": now, "total": 1.0}) + "\n")
    assert mod.main(["--home", str(tmp_path), "--live-log", str(live)]) == 2
