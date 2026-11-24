"""root-fs-stall: stalls on the root drive go RED; a quiet or missing probe is UNKNOWN, never OK."""
from __future__ import annotations

import importlib.util
import json
import os
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


def test_burst_reports_peak_pressure_from_every_stall_not_only_the_worst():
    stalls = [rec(event="slow", at=NOW - 400, total=23.6, fsync=23.58, psi_io=57.37, psi_mem=0.0),
              rec(event="slow", at=NOW - 200, total=14.9, fsync=14.89, psi_io=93.23, psi_mem=0.22)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2
    assert "worst 23.6s" in lines[0] and "io_psi 57.37" in lines[0]   # longest stall's own reading
    assert "peak io_psi 93.23" in lines[0]                            # higher pressure on a shorter stall
    assert "peak mem_psi 0.22" in lines[0]                            # pressure hidden by a worst-only reading


def test_single_stall_does_not_repeat_peak_pressure():
    code, lines = mod.evaluate(
        [rec(), rec(event="slow", at=NOW - 60, total=17.0, fsync=16.97, psi_io=84.66, psi_mem=23.93)], NOW)
    assert code == 2 and "peak" not in lines[0]


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


def test_two_bursts_inside_one_window_are_named():
    stalls = [rec(event="slow", at=NOW - 800, total=9.9, fsync=9.9),
              rec(event="slow", at=NOW - 100, total=8.1, fsync=8.1)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2
    assert "2 separate bursts (gaps >= 2 min)" in lines[0]
    assert "newest 02:45:00Z" in lines[0]


def test_nearby_episodes_are_one_burst():
    stalls = [rec(event="slow", at=NOW - 600, total=9.9, fsync=9.9),
              rec(event="slow", at=NOW - 500, total=8.1, fsync=8.1)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2 and "separate bursts" not in lines[0]     # 100s gap is one burst


def test_gap_just_above_threshold_is_a_second_burst():
    stalls = [rec(event="slow", at=NOW - 600, total=9.9, fsync=9.9),
              rec(event="slow", at=NOW - 470, total=8.1, fsync=8.1)]  # 130s > 120s
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2 and "2 separate bursts" in lines[0]


def test_multiprobe_cliff_does_not_inflate_burst_count():
    stalls = [rec(event="slow", at=NOW - 700 + i * 0.01, total=9.9, fsync=9.9) for i in range(3)]
    stalls += [rec(event="slow", at=NOW - 100 + i * 0.01, total=8.1, fsync=8.1) for i in range(3)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 2 and "2 separate bursts" in lines[0]       # de-dup happens before grouping


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


def test_green_recurrence_carries_worst_peak_pressure_and_burst_count():
    stalls = [rec(event="slow", at=NOW - 5000 + i * 400, total=9.9 + i, fsync=9.9 + i,
                  psi_io=40.0 + i, psi_mem=0.1 * (i + 1)) for i in range(3)]
    stalls.append(rec(event="slow", at=NOW - 5000 + 0.01, total=9.9, psi_io=40.0, psi_mem=0.1))
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 0
    assert "worst 11.9s" in lines[0]
    assert "peak io_psi 42.00" in lines[0] and "peak mem_psi 0.30" in lines[0]
    assert "3 separate bursts (gaps >= 2 min)" in lines[0]
    assert ", newest " not in lines[0]                       # GREEN already says "most recent X min ago"


def test_green_recurrence_single_stall_shows_its_own_pressure():
    code, lines = mod.evaluate(
        [rec(), rec(event="slow", at=NOW - 5000, total=9.9, psi_io=84.66, psi_mem=23.93)], NOW)
    assert code == 0 and "worst 9.9s" in lines[0]
    assert "io_psi 84.66" in lines[0] and "mem_psi 23.93" in lines[0]
    assert "peak" not in lines[0] and "separate bursts" not in lines[0]


def test_green_recurrence_two_bursts_do_not_repeat_the_newest_stamp():
    stalls = [rec(event="slow", at=NOW - 5000, total=9.9),
              rec(event="slow", at=NOW - 960, total=9.9)]
    code, lines = mod.evaluate([rec()] + stalls, NOW)
    assert code == 0 and "2 separate bursts" in lines[0]
    assert ", newest " not in lines[0] and "most recent 16 min ago" in lines[0]


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
    own = os.path.realpath(tmp_path)
    live.write_text(json.dumps({"dir": "root", "event": "slow", "at": now - 60, "total": 9.9, "source": own}) + "\n"
                    + json.dumps({"dir": "root", "event": "minute", "at": now, "total": 1.0, "source": own}) + "\n")
    assert mod.main(["--home", str(tmp_path), "--live-log", str(live)]) == 2


def test_main_ignores_another_plants_rows(tmp_path):
    # Another plant's probe (its own source, or the old unstamped format) must not make this plant RED.
    live = tmp_path / "live.jsonl"
    now = time.time()
    other = os.path.realpath(tmp_path / "other-plant")
    live.write_text(json.dumps({"dir": "root", "event": "slow", "at": now - 60, "total": 9.9, "source": other}) + "\n"
                    + json.dumps({"dir": "root", "event": "slow", "at": now - 30, "total": 9.9}) + "\n"
                    + json.dumps({"dir": "root", "event": "minute", "at": now, "total": 1.0, "source": os.path.realpath(tmp_path)}) + "\n")
    assert mod.main(["--home", str(tmp_path), "--live-log", str(live)]) == 0


def test_green_line_names_the_sub_threshold_band_without_going_red():
    code, lines = mod.evaluate(
        [rec(), rec(event="slow", at=NOW - 60, total=0.45, fsync=0.44),
         rec(event="slow", at=NOW - 3600, total=0.36, fsync=0.35),
         rec(event="slow", at=NOW - 3600 + 0.2, total=0.5, fsync=0.5, dir_="root")], NOW)
    assert code == 0
    assert "sub-threshold band 0.3-5 s: 2 probe-second(s)" in lines[0]
    assert "worst 0.50s" in lines[0]


def test_band_ignores_probes_below_its_floor_and_stalls_above_it():
    code, lines = mod.evaluate([rec(), rec(event="slow", at=NOW - 60, total=0.1)], NOW)
    assert code == 0 and "sub-threshold" not in lines[0]


def test_quiet_line_names_a_rising_rate_of_one_second_probes():
    baseline = [rec(event="slow", at=NOW - 1800 * i - 5, total=1.5) for i in range(1, 13)]
    recent = [rec(event="slow", at=NOW - 60 * i - 1, total=1.2) for i in range(8)]
    code, lines = mod.evaluate([rec(), *baseline, *recent], NOW)
    assert code == 0
    assert "probes >= 1 s: 8 in the last 30 min vs 6 h median 1 per 30 min (rising)" in lines[0]


def test_rate_line_is_omitted_when_no_slow_probes():
    code, lines = mod.evaluate([rec()], NOW)
    assert code == 0 and "probes >=" not in lines[0]
