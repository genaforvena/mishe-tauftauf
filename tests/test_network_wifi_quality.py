"""Wi-Fi transmission counters: decoded from read-only iwpriv lines, rate between two reads, UNKNOWN guards."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


boundary = load(REPO / "tools" / "network_boundary.py", "network_boundary_wifi_quality_under_test")
view = load(REPO / "tools" / "network_view.py", "network_view_wifi_quality_under_test")

STAT_LINES = [
    "ra0       stat:",
    "CurrentTemperature              = 61",
    "Tx success                      = 64996413",
    "Tx fail count                   = 11111324, PER=3.3%",
    "Rx success                      = 94293192",
    "Rx with CRC                     = 17635455, PER=0.4%",
    "Last TX Rate					= MCS14, BW20, 0.4us GI, HT_MM, LDPC",
]
NOW = 1_800_000_000.0


def test_decode_counts_and_temperature():
    assert boundary.decode_radio_stats(STAT_LINES) == {
        "tx_ok": 64996413, "tx_fail": 11111324, "rx_ok": 94293192, "rx_crc": 17635455,
        "temp_c": 61, "tx_per": 3.3}


def test_decode_keeps_the_drivers_own_per_apart_from_the_counters():
    stats = boundary.decode_radio_stats(["Tx fail count = 11221647, PER=0.1%"])
    assert stats == {"tx_fail": 11221647, "tx_per": 0.1}
    # an older read without the PER field decodes the counter and no invented rate
    assert boundary.decode_radio_stats(["Tx fail count = 5"]) == {"tx_fail": 5}


def test_decode_without_counters_is_none_not_zero():
    assert boundary.decode_radio_stats(["ra0       stat:", "Last TX Rate = MCS14"]) is None


def radio(band, stats):
    return {"band": band, "channel": 13, "freq_ghz": 2.472, "noise_dbm": -55, "stats": stats}


def router_row(ts, uptime, stats_24, stats_5=None):
    return {"ts": ts, "ok": True, "uptime_s": uptime,
            "radios": [radio("2.4", stats_24), radio("5", stats_5 or {"tx_ok": 1000, "tx_fail": 0})]}


def test_line_shows_since_boot_and_the_last_hour(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    import json
    rows = [router_row(NOW - 3000, 5000, {"tx_ok": 100000, "tx_fail": 3000}),
            router_row(NOW - 60, 7940, {"tx_ok": 160000, "tx_fail": 3600, "temp_c": 61})]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.wifi_quality_line(now=NOW)
    assert "2.4 GHz 2.2% of attempts failed since boot" in line, line
    assert "1.0% last 49 min" in line, line
    assert "radio 61 C" in line and "read 60 s ago" in line, line


def test_each_band_is_its_own_line_and_carries_the_drivers_frame_loss(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    import json
    rows = [router_row(NOW - 3000, 5000, {"tx_ok": 100000, "tx_fail": 3000, "tx_per": 3.3}),
            router_row(NOW - 60, 7940, {"tx_ok": 160000, "tx_fail": 3600, "temp_c": 61,
                                        "tx_per": 3.3},
                       {"tx_ok": 5000, "tx_fail": 300, "tx_per": 0.1})]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    lines = view.wifi_quality_line(now=NOW).split("\n")
    assert len(lines) == 3, lines
    assert lines[0].startswith("2.4 GHz ") and "driver's error rate 3.3%" in lines[0], lines
    assert lines[1].startswith("5 GHz ") and "driver's error rate 0.1%" in lines[1], lines
    # the caveat and the age sit on their own trailing line so no band line overflows the pane
    assert lines[2] == "radio 61 C; driver's error rate window not measured; read 60 s ago", lines
    # a read without the driver's PER keeps the attempts figure, invents no rate, and says UNKNOWN
    # rather than dropping the clause: an absent number must not read as a quiet radio
    rows[-1]["radios"][0]["stats"].pop("tx_per")
    rows[-1]["radios"][1]["stats"].pop("tx_per")
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    line = view.wifi_quality_line(now=NOW)
    assert "since boot" in line, line
    assert line.count("driver's error rate UNKNOWN") == 2, line



def test_line_is_unknown_when_the_counters_are_missing(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    import json
    rows = [{"ts": NOW - 60, "ok": True, "uptime_s": 900,
             "radios": [{"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -55}]}]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert view.wifi_quality_line(now=NOW).startswith("UNKNOWN")


def test_line_falls_back_to_the_newest_read_that_carries_counters(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    import json
    rows = [router_row(NOW - 300, 5000, {"tx_ok": 1000, "tx_fail": 10, "temp_c": 58}),
            {"ts": NOW - 60, "ok": True, "uptime_s": 5840,
             "radios": [{"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -55}]}]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.wifi_quality_line(now=NOW)
    assert "since boot" in line and "read 300 s ago" in line, line


def test_line_names_a_band_whose_counters_are_missing(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    import json
    rows = [{"ts": NOW - 60, "ok": True, "uptime_s": 900,
             "radios": [radio("2.4", {"tx_ok": 1000, "tx_fail": 10}),
                        {"band": "5", "channel": 40, "freq_ghz": 5.2, "noise_dbm": -56}]}]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.wifi_quality_line(now=NOW)
    assert "2.4 GHz 1.0% of attempts failed since boot" in line, line
    assert "5 GHz transmission counters UNKNOWN (missing from the newest read)" in line, line


def test_line_names_a_band_that_vanished_from_the_newest_read(tmp_path, monkeypatch):
    # a row from before a silent radio was kept: the band is simply not there, and must not be silent
    rows = [{"ts": NOW - 60, "ok": True, "uptime_s": 900,
             "radios": [radio("2.4", {"tx_ok": 1000, "tx_fail": 10, "tx_per": 1.0})]}]
    write_rows(tmp_path, monkeypatch, rows)
    line = view.wifi_quality_line(now=NOW)
    assert "2.4 GHz 1.0% of attempts failed since boot" in line, line
    assert "5 GHz transmission counters UNKNOWN (radio missing from the newest read)" in line, line


def test_line_is_unknown_and_stale_for_an_old_read(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    import json
    rows = [router_row(NOW - view.STALE_ROUTER_S - 60, 5000, {"tx_ok": 1000, "tx_fail": 10})]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.wifi_quality_line(now=NOW)
    assert line.startswith("UNKNOWN") and line.endswith("STALE"), line


def test_a_reboot_leaves_the_last_hour_rate_unknown(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    import json
    rows = [router_row(NOW - 3000, 10000, {"tx_ok": 1000, "tx_fail": 10}),
            router_row(NOW - 60, 200, {"tx_ok": 20, "tx_fail": 1})]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.wifi_quality_line(now=NOW)
    assert "since boot" in line and "(last hour UNKNOWN)" in line, line


USUAL_NOW = NOW + 1200.0  # twenty minutes past the clock hour, so "last hour" and the completed hours differ


def counter_history(now, hours, share, step=300, gap=None):
    """Router reads with cumulative counters at `step`, each band failing `share(t, now)` of its sends.

    `gap` is an optional (lo, hi) span with no reads at all, the shape of a producer that stopped.
    """
    rows, c24, c5 = [], {"tx_ok": 0, "tx_fail": 0}, {"tx_ok": 0, "tx_fail": 0}
    t, uptime = now - hours * 3600, 100000.0
    while t <= now:
        if gap is None or not (gap[0] <= t <= gap[1]):
            for counters in (c24, c5):
                sends = 1000
                failed = int(sends * share(t, now))
                counters["tx_ok"] += sends - failed
                counters["tx_fail"] += failed
            rows.append(router_row(t, uptime, dict(c24, temp_c=59), dict(c5)))
        t += step
        uptime += step
    return rows


def write_rows(tmp_path, monkeypatch, rows):
    import json
    log = tmp_path / "router.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)


def test_a_bad_hour_is_named_much_worse_than_its_own_usual(tmp_path, monkeypatch):
    # ten hours at 5% (one of them a hole, so a gap is not read as a calm 0%), the last 65 min at 25%
    rows = counter_history(USUAL_NOW, 12,
                           lambda t, now: 0.25 if t >= now - 3900 else 0.05,
                           gap=(USUAL_NOW - 6 * 3600, USUAL_NOW - 4 * 3600))
    write_rows(tmp_path, monkeypatch, rows)
    line = view.wifi_quality_line(now=USUAL_NOW).split("\n")[0]
    assert "25.0% last" in line, line
    # the flagged hour is named with its own usual; the duration clause is covered by the tests below
    assert ", much worse than usual" in line and "(usual 5.0%)" in line, line


def test_a_typical_hour_says_about_usual(tmp_path, monkeypatch):
    rows = counter_history(USUAL_NOW, 12, lambda t, now: 0.14)
    write_rows(tmp_path, monkeypatch, rows)
    line = view.wifi_quality_line(now=USUAL_NOW).split("\n")[0]
    assert ", about usual" in line, line
    assert "usual" not in line.split(", about usual")[1], line


def test_without_completed_hours_there_is_no_usual_yet(tmp_path, monkeypatch):
    rows = counter_history(USUAL_NOW, 0.25, lambda t, now: 0.30)
    write_rows(tmp_path, monkeypatch, rows)
    line = view.wifi_quality_line(now=USUAL_NOW).split("\n")[0]
    assert ", no usual yet" in line, line
    assert "worse than usual" not in line, line


def test_a_quiet_band_needs_the_points_above_and_not_only_the_ratio():
    # 5 GHz is usually around 1.5%; triple that is 4.5%, still noise on a quiet band
    assert view.wifi_usuality(4.5, [1.5, 1.6, 1.4]) == ", about usual"
    # but a share far above it is named, with the usual it is compared against
    assert view.wifi_usuality(16.0, [1.5, 1.6, 1.4]) == ", much worse than usual (usual 1.5%)"


def test_a_multi_hour_bad_streak_names_its_age(tmp_path, monkeypatch):
    # Twelve hours at 5%, the last five at 25%: five completed hours cleared the same much-worse bar.
    rows = counter_history(USUAL_NOW, 12,
                           lambda t, now: 0.25 if t >= now - 5 * 3600 else 0.05)
    write_rows(tmp_path, monkeypatch, rows)
    line = view.wifi_quality_line(now=USUAL_NOW).split("\n")[0]
    assert ", much worse than usual for at least 5 h (usual 5.0%)" in line, line


def test_a_one_hour_worse_blip_has_no_duration(tmp_path, monkeypatch):
    # 11% against 5% clears 'worse' but not 'much worse'; only the current hour is elevated.
    rows = counter_history(USUAL_NOW, 12,
                           lambda t, now: 0.11 if t >= now - 3600 else 0.05)
    write_rows(tmp_path, monkeypatch, rows)
    line = view.wifi_quality_line(now=USUAL_NOW).split("\n")[0]
    assert ", worse than usual (usual 5.0%)" in line, line


def test_duration_counts_only_the_unbroken_run():
    # A calm hour inside the recent hours stops the count, even if older hours were bad.
    assert view.wifi_usuality(25.0, [25.0, 25.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0]) == \
        ", much worse than usual for at least 2 h (usual 5.0%)"
    # A 'worse' hour counts a prior 'much worse' hour: the lower bar was cleared too.
    assert view.wifi_usuality(11.0, [25.0, 11.0, 5.0, 5.0, 5.0]) == \
        ", worse than usual for at least 2 h (usual 5.0%)"


def test_a_thirteen_hour_bad_run_does_not_become_its_own_usual():
    # Thirteen hours at 15% against eleven at 5%: the trailing median is 15%, so the old
    # comparison called the still-busy radio "about usual" and the streak vanished.
    text = view.wifi_usuality(15.2, [15.0] * 13 + [5.0] * 11, 6.0)
    assert "worse than usual for at least 13 h (usual 5.0%)" in text, text


def test_a_whole_window_bad_uses_the_lifetime_share():
    text = view.wifi_usuality(15.2, [15.0] * 24, 6.0)
    assert "worse than usual for at least 24 h (usual 6.0% since boot)" in text, text


def test_an_elevated_hour_still_counts_plain_worse_hours():
    # 16% against 5% is 'much worse'; the two 11% hours cleared only 'worse' but still
    # extend the age, which must grow rather than reset when the label changes level.
    text = view.wifi_usuality(16.0, [11.0, 11.0, 5.0, 5.0, 5.0], 5.0)
    assert "much worse than usual for at least 2 h (usual 5.0%)" in text, text


def test_a_noisy_band_without_a_run_keeps_its_plain_median():
    rates = [10.8, 13.5, 13.2, 16.1, 10.3, 15.8, 14.3, 14.8] + [14.0] * 16
    assert view.wifi_usuality(10.8, rates, 14.2) == ", about usual"


def test_a_whole_window_bad_names_the_lifetime_baseline(tmp_path, monkeypatch):
    rows = counter_history(USUAL_NOW, 50,
                           lambda t, now: 0.25 if t >= now - 26 * 3600 else 0.05)
    write_rows(tmp_path, monkeypatch, rows)
    line = view.wifi_quality_line(now=USUAL_NOW).split("\n")[0]
    assert "for at least 24 h" in line and "since boot" in line, line
