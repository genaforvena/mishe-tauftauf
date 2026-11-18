"""The network window's reading of probe rows: a failed or missing row must never look calm.

These are the view's tests (tools/network_view.py, tools/network_boundary.py).
The probe that writes the rows is a separate responsibility with its own
contract and its own tests (tests/test_mishe_network_probe.py).
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
view = load(REPO / "tools" / "network_view.py", "network_view_under_test")


def test_view_verdict_down_when_no_secure_site_answers():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"at": now, "gateway": {"loss_pct": 0.0, "avg_ms": 0.4},
           "internet": {"gstatic": {"ok": False, "curl_exit": 28}, "cloudflare": {"ok": False, "curl_exit": 28}}}
    assert view.verdict(row, 0).startswith("DOWN: router answers")


def test_view_verdict_unknown_for_old_format_and_stale_rows():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old = {"at": now, "gateway": {"loss_pct": 0.0}, "cloudflare": {"loss_pct": 0.0}}
    assert view.verdict(old, 0).startswith("UNKNOWN")
    fresh = {"at": now, "gateway": {"loss_pct": 0.0}, "internet": {"gstatic": {"ok": True, "total_ms": 300.0}}}
    assert view.verdict(fresh, 3600).startswith("UNKNOWN")  # stale probe is never a calm network
    assert view.verdict(fresh, 0) == "OK"


def test_wifi_decode_counts_clients_per_band_and_drops_macs():
    text = ("BAND 2.472\n7C:C3:A1:A5:20:D7  -28 dBm / unknown (SNR -28)  0 ms ago\n"
            "3A:2D:71:4B:CC:FC  -80 dBm / unknown  0 ms ago\nBAND 5.200\n")
    bands = view.nb.decode_wifi(text)
    assert bands == {"2.4": [-28, -80], "5": []}
    assert "7C:C3" not in repr(bands)


def test_band_name_maps_channel_frequency_to_band():
    nb = view.nb
    assert nb.band_name("2.472") == "2.4"
    assert nb.band_name("5.200") == "5"
    assert nb.band_name("6.135") == "6"
    assert nb.band_name("junk") == "junk"


def test_window_band_labels_match_the_router_rows():
    # The window's "2.4 GHz" / "5 GHz" labels and the band field in router.jsonl must be the same
    # strings: the window reads both, and a label drift would split one band into two in the view.
    import network_router as nr
    assert view.nb.band_name("2.472") == nr.BANDS["ra0"]
    assert view.nb.band_name("5.200") == nr.BANDS["rax0"]


def test_wifi_decode_rejects_empty_listing():
    import pytest
    with pytest.raises(ValueError):
        view.nb.decode_wifi("")


def test_wifi_decode_ids_carry_the_recorded_client_id_not_the_mac():
    # The live listing is the only place a MAC appears; it is mapped through the same short hash the
    # router row stores, so the window can name a client from the recorded read.
    import network_router as nr
    mac, other = "7C:C3:A1:A5:20:D7", "3A:2D:71:4B:CC:FC"
    text = (f"BAND 2.472\n{mac}  -28 dBm / unknown (SNR -28)  0 ms ago\n"
            f"{other}  -80 dBm / unknown  0 ms ago\nBAND 5.200\n")
    bands = view.nb.decode_wifi_ids(text, nr.client_id)
    assert bands == {"2.4": [(nr.client_id(mac), -28), (nr.client_id(other), -80)], "5": []}
    assert "7C:C3" not in repr(bands)


def _wifi_read(monkeypatch, stdout):
    monkeypatch.setattr(view.subprocess, "run",
                        lambda *args, **kwargs: view.subprocess.CompletedProcess(args, 0, stdout, ""))
    monkeypatch.setitem(view._wifi, "at", 0.0)


def test_live_wifi_line_names_the_weakest_device_the_recorded_read_knows(tmp_path, monkeypatch):
    import network_router as nr
    mac = "7C:C3:A1:A5:20:D7"
    _router_log(tmp_path, monkeypatch,
                {"ts": time.time(), "ok": True,
                 "clients": [{"id": nr.client_id(mac), "name": "iMac-Rozalia", "band": "2.4", "dbm": -81}]})
    _wifi_read(monkeypatch, f"BAND 2.472\n{mac}  -81 dBm / unknown (SNR -81)  0 ms ago\nBAND 5.200\n")
    text, weak = view.wifi_facts()
    assert "weakest iMac-Rozalia -81 dBm on 2.4 GHz" in text
    assert weak is True


def test_live_wifi_line_keeps_the_bare_dbm_when_no_recorded_name_matches(tmp_path, monkeypatch):
    _router_log(tmp_path, monkeypatch, {"ts": time.time(), "ok": True, "clients": []})
    _wifi_read(monkeypatch, "BAND 2.472\n7C:C3:A1:A5:20:D7  -81 dBm / unknown  0 ms ago\nBAND 5.200\n")
    text, weak = view.wifi_facts()
    assert "weakest -81 dBm on 2.4 GHz" in text
    assert weak is True


def test_live_wifi_line_names_a_band_its_listing_did_not_show(tmp_path, monkeypatch):
    # the router has one access point per band; a listing that showed only one is a gap, not "0 clients"
    _router_log(tmp_path, monkeypatch, {"ts": time.time(), "ok": True, "clients": []})
    _wifi_read(monkeypatch, "BAND 2.472\n7C:C3:A1:A5:20:D7  -81 dBm / unknown  0 ms ago\n")
    text, _weak = view.wifi_facts()
    assert "2.4 GHz 1" in text and "5 GHz UNKNOWN" in text, text


def _probe(at, down):
    internet = {"gstatic": {"ok": not down, "total_ms": None if down else 300.0},
                "cloudflare": {"ok": not down, "total_ms": None if down else 280.0}}
    return {"at": at, "gateway": {"loss_pct": 0.0, "avg_ms": 0.4}, "internet": internet, "dns_ms": 50.0}


def test_outage_episodes_list_start_and_end_and_still_down():
    data = [_probe("2026-11-10T10:00:00Z", False), _probe("2026-11-10T10:01:00Z", True),
            _probe("2026-11-10T10:02:00Z", True), _probe("2026-11-10T10:05:00Z", False),
            _probe("2026-11-10T10:06:00Z", True)]
    assert view.outage_episodes(data) == [("2026-11-10T10:01:00Z", "2026-11-10T10:05:00Z"),
                                          ("2026-11-10T10:06:00Z", None)]
    lines = view.outage_lines(data)
    assert lines[0] == "  DOWN 10:01Z to 10:05Z (4 min)"
    assert lines[1] == "  DOWN since 11-10 10:06Z, still down at the last probe"


def test_outage_episode_across_midnight_carries_both_dates():
    data = [_probe("2026-11-10T23:50:00Z", False), _probe("2026-11-10T23:58:00Z", True),
            _probe("2026-11-11T00:06:00Z", False)]
    assert view.outage_lines(data)[0] == "  DOWN 11-10 23:58Z to 11-11 00:06Z (8 min)"


def test_outage_span_names_its_dates_and_length_not_just_times():
    data = [_probe("2026-11-14T16:54:05Z", False), _probe("2026-11-18T02:02:55Z", False)]
    assert view.outage_lines(data) == ["  none over the last 2 probes in 3.4 days (11-14 16:54Z to 11-18 02:02Z)"]


def test_outage_lines_say_none_when_all_probes_answer_and_unknown_without_rows():
    data = [_probe("2026-11-10T10:00:00Z", False), _probe("2026-11-10T10:01:00Z", False)]
    assert view.outage_lines(data)[0].startswith("  none over the last 2 probes")
    assert view.outage_lines([])[0].startswith("  UNKNOWN")


def test_outage_skips_rows_from_before_the_secure_web_check():
    old = {"at": "2026-11-10T09:00:00Z", "gateway": {"loss_pct": 0.0, "avg_ms": 0.4}}
    assert view.outage_episodes([old, _probe("2026-11-10T09:01:00Z", False)]) == []


def test_recorded_clients_line_unknown_without_log(tmp_path, monkeypatch):
    monkeypatch.setattr(view, "ROUTER_LOG", tmp_path / "router.jsonl")
    assert view.recorded_clients_line().startswith("UNKNOWN")


def test_recorded_clients_line_shows_count_weakest_and_age(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    now = 1_000_000.0
    clients = [{"band": "2.4", "dbm": -30, "name": "iPad", "id": "a", "rx": 1, "tx": 1},
               {"band": "5", "dbm": -36, "name": "Redmi-10", "id": "b", "rx": 1, "tx": 1}]
    log.write_text('{"ts": %s, "ok": true, "clients": %s}\n' % (now - 600, __import__("json").dumps(clients)))
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.recorded_clients_line(now)
    assert "2 clients" in line and "weakest Redmi-10 -36 dBm" in line and "read 600 s ago" in line
    assert "STALE" not in line


def test_recorded_clients_line_reports_failed_read_not_calm(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000000.0, "ok": false, "error": "ssh timeout"}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.recorded_clients_line(1000000.0 + 60)
    assert line.startswith("UNKNOWN") and "ssh timeout" in line


def test_recorded_clients_line_marks_stale(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000000.0, "ok": true, "clients": []}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert "STALE" in view.recorded_clients_line(1000000.0 + 3600)


def test_gateway_line_shows_jitter_and_unknown_when_missing():
    rows = [{"gateway": {"loss_pct": 0.0, "avg_ms": 0.47, "jitter_ms": 0.027}}]
    line = view.gateway_line(rows)
    assert "jitter 0.03 ms" in line
    old = [{"gateway": {"loss_pct": 0.0, "avg_ms": 0.47}}]
    assert "jitter UNKNOWN" in view.gateway_line(old)
    assert "jitter UNKNOWN" in view.gateway_line([{"gateway": None}])
    assert "jitter UNKNOWN" in view.gateway_line([])


def _router_log(tmp_path, monkeypatch, row):
    import json
    log = tmp_path / "router.jsonl"
    rows = row if isinstance(row, list) else [row]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(view, "ROUTER_LOG", log)


def test_uplink_line_shows_wan_errors_and_drops_with_age(tmp_path, monkeypatch):
    row = {"ts": 1000000.0, "ok": True, "clients": [],
           "uplink": {"device": "eth0", "rx_errors": 7, "rx_dropped": 42, "tx_errors": 3, "tx_dropped": 5}}
    _router_log(tmp_path, monkeypatch, row)
    line = view.uplink_line(1000000.0 + 60)
    assert "eth0" in line and "rx errors 7, dropped 42" in line and "tx errors 3, dropped 5" in line
    assert "read 60 s ago" in line and "STALE" not in line


def test_uplink_line_unknown_when_counters_missing(tmp_path, monkeypatch):
    _router_log(tmp_path, monkeypatch, {"ts": 1000000.0, "ok": True, "clients": [], "uplink": None})
    assert view.uplink_line(1000000.0 + 60).startswith("UNKNOWN")


def test_uplink_line_failed_read_is_unknown_not_zero(tmp_path, monkeypatch):
    _router_log(tmp_path, monkeypatch, {"ts": 1000000.0, "ok": False, "error": "ssh exit 255"})
    line = view.uplink_line(1000000.0 + 60)
    assert line.startswith("UNKNOWN") and "rx errors" not in line


def _uplink_read(ts, uptime, rx_dropped):
    return {"ts": ts, "ok": True, "uptime_s": uptime, "clients": [],
            "uplink": {"device": "eth0", "rx_errors": 0, "rx_dropped": rx_dropped, "tx_errors": 0, "tx_dropped": 0}}


def _wan_read(ts, carrier_down, uptime=1073362, speed=1000, duplex="full"):
    row = _uplink_read(ts, uptime, 100)
    row["wan_link"] = {"device": "eth0", "speed_mbps": speed, "duplex": duplex,
                       "carrier_up": carrier_down, "carrier_down": carrier_down}
    return row


def test_wan_link_line_shows_negotiated_speed_and_quiet_drops(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, [_wan_read(now - 1800, 2), _wan_read(now, 2)])
    line = view.wan_link_line(now)
    assert "eth0: 1000 Mb/s full duplex" in line
    assert "link down 2 times since boot (12.4 days)" in line
    assert "none in the last hour" in line
    assert "read 0 s ago" in line


def test_wan_link_line_counts_a_drop_in_the_last_hour(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, [_wan_read(now - 1800, 2), _wan_read(now, 3)])
    assert ", 1 in the last hour" in view.wan_link_line(now)


def test_wan_link_line_marks_a_reset_after_a_router_reboot(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, [_wan_read(now - 1800, 7), _wan_read(now, 1, uptime=900)])
    line = view.wan_link_line(now)
    assert "at least 1 in the last hour (counters reset by a reboot)" in line
    assert "in the last hour," not in line


def test_wan_link_line_counts_across_a_reset_inside_the_hour(tmp_path, monkeypatch):
    # 3 -> 4 (1 drop), reboot, 2 since: 3 in the hour, not the endpoint difference of -1.
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, [_wan_read(now - 2400, 3), _wan_read(now - 1200, 4),
                                        _wan_read(now, 2, uptime=900)])
    assert ", at least 3 in the last hour (counters reset by a reboot)" in view.wan_link_line(now)


def test_wan_link_line_unknown_without_physical_state(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, {"ts": now, "ok": True, "clients": [], "uplink": None})
    assert view.wan_link_line(now).startswith("UNKNOWN")
    _router_log(tmp_path, monkeypatch, [_wan_read(now - 60, 2, speed=None)])
    assert view.wan_link_line(now).startswith("UNKNOWN (eth0 link speed not reported")


def test_uplink_rate_over_the_last_hour(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, _uplink_read(now - 3000, 100000, 1000))
    log = tmp_path / "router.jsonl"
    log.write_text(log.read_text() + json.dumps(_uplink_read(now, 103000, 1300)) + "\n")
    assert "300 receive drops in the last 50 min (about 360 per hour)" in view.uplink_rate_line(now)


def test_uplink_rate_restarts_after_a_reboot(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, _uplink_read(now - 3000, 90000, 5000))
    log = tmp_path / "router.jsonl"
    log.write_text(log.read_text() + json.dumps(_uplink_read(now - 2000, 1000, 10)) + "\n"
                   + json.dumps(_uplink_read(now, 3000, 40)) + "\n")
    # after the reboot the span is 2000 s with 30 drops: the pre-reboot counter is not subtracted
    assert "30 receive drops" in view.uplink_rate_line(now)


def _hourly_reads(now, per_hour_rates, uptime0=100000):
    """Reads every 10 min over len(per_hour_rates) hours ending at now; rates are drops per hour, oldest first."""
    start = now - len(per_hour_rates) * 3600
    reads, drops, t = [], 0.0, start
    while t <= now:
        reads.append(_uplink_read(t, uptime0 + (t - start), int(drops)))
        hour = min(int((t - start) // 3600), len(per_hour_rates) - 1)
        drops += per_hour_rates[hour] * 600 / 3600
        t += 600
    return reads


def _write_reads(tmp_path, monkeypatch, reads):
    log = tmp_path / "router.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in reads))
    monkeypatch.setattr(view, "ROUTER_LOG", log)


def test_uplink_trend_says_same_when_the_hours_are_flat(tmp_path, monkeypatch):
    now = 1_000_000.0
    reads = _hourly_reads(now, [330] * 7)
    _write_reads(tmp_path, monkeypatch, reads)
    line = view.uplink_rate_line(now)
    assert "about 330 per hour" in line and "about the same as the 6 earlier hours" in line


def test_uplink_trend_says_higher_when_this_hour_jumped(tmp_path, monkeypatch):
    now = 1_000_000.0
    reads = _hourly_reads(now, [100] * 6 + [600])
    _write_reads(tmp_path, monkeypatch, reads)
    assert "higher than the 6 earlier hours" in view.uplink_rate_line(now)


def test_uplink_trend_needs_three_earlier_hours(tmp_path, monkeypatch):
    now = 1_000_000.0
    reads = _hourly_reads(now, [330] * 2)
    _write_reads(tmp_path, monkeypatch, reads)
    assert "no comparison (fewer than 3 earlier hours" in view.uplink_rate_line(now)


def test_uplink_rate_unknown_with_one_read(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, _uplink_read(now, 3000, 40))
    assert view.uplink_rate_line(now).startswith("UNKNOWN")


def test_router_health_flags_the_self_limit(tmp_path, monkeypatch):
    now = 1_000_000.0
    row = {"ts": now, "ok": True, "clients": [], "router_load": {"load_1m": 1.4, "mem_available_mb": 300.0}}
    _router_log(tmp_path, monkeypatch, row)
    line = view.router_health_line(now)
    assert "load 1.40" in line and "over the self-limit" in line


def test_router_health_shows_the_skip_reason(tmp_path, monkeypatch):
    now = 1_000_000.0
    reason = "skipped: router over its self-limit at the last read (load 1.08 (5 min), free RAM 196 MB)"
    _router_log(tmp_path, monkeypatch, {"ts": now, "ok": False, "error": reason})
    line = view.router_health_line(now)
    assert line.startswith("UNKNOWN") and "skipped: router over its self-limit" in line


def test_router_health_keeps_the_last_real_load_after_a_skip(tmp_path, monkeypatch):
    now = 1_000_000.0
    real = {"ts": now - 1800, "ok": True, "clients": [], "router_load": {"load_5m": 0.31, "mem_available_mb": 207.0}}
    skip = {"ts": now, "ok": False, "error": "skipped: router over its self-limit at the last read"}
    _router_log(tmp_path, monkeypatch, real)
    with open(view.ROUTER_LOG, "a") as fh:
        fh.write(json.dumps(skip) + "\n")
    line = view.router_health_line(now)
    assert line.startswith("UNKNOWN") and "skipped: router over its self-limit" in line
    assert "last real load 0.31 (5 min), free RAM 207 MB, read 30 min ago" in line


def test_router_health_unknown_without_numbers(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, {"ts": now, "ok": True, "clients": [], "router_load": None})
    assert view.router_health_line(now).startswith("UNKNOWN")


def _load_rows(now, loads, step=300):
    rows = [{"ts": now - step * (len(loads) - 1 - i), "ok": True,
             "router_load": {"load_5m": load, "mem_available_mb": 220.0}}
            for i, load in enumerate(loads)]
    return rows


def test_router_health_counts_how_long_the_breach_has_lasted(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, _load_rows(now, [0.2] * 3 + [1.5] * 6))  # 25 minutes over
    line = view.router_health_line(now)
    assert "over the self-limit for 25 min" in line and "alarm at 30 min" in line


def test_router_health_drops_the_alarm_note_past_thirty_minutes(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, _load_rows(now, [1.5] * 7))  # 30 minutes over
    line = view.router_health_line(now)
    assert "over the self-limit for 30 min" in line and "alarm at" not in line


def test_router_health_forgets_a_breach_that_ended(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, _load_rows(now, [1.5] * 4 + [0.2]))  # spike ended 0 min ago
    line = view.router_health_line(now)
    assert "within the self-limit" in line and "over the self-limit" not in line


def _web_rows(n, ms, ok=True):
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return [{"at": at, "internet": {name: {"ok": ok, "total_ms": ms if ok else None}
                                    for name in ("gstatic", "cloudflare")}} for _ in range(n)]


def test_usuality_unknown_with_no_rows():
    assert view.usuality_line([]).startswith("UNKNOWN")


def test_usuality_unknown_with_too_few_samples():
    line = view.usuality_line(_web_rows(10, 300))
    assert line.startswith("UNKNOWN") and "10" in line and "120" in line


def test_usuality_flat_day_is_about_usual():
    line = view.usuality_line(_web_rows(200, 300))
    assert "about usual" in line and "median 300 ms vs typical 300 ms" in line


def test_usuality_names_the_window_it_measured_instead_of_saying_now():
    # The INTERNET lines show the newest probe; USUAL is the median of the last ten probes, which at
    # the probe cadence is about half an hour. A second "now" made the pane look self-contradictory.
    now = time.time()
    rows = []
    for i in range(200):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (199 - i) * 204))  # ~3.4 min apart
        rows.append({"at": at, "internet": {name: {"ok": True, "total_ms": 300.0}
                                            for name in ("gstatic", "cloudflare")}})
    line = view.usuality_line(rows, now=now)
    assert "last 10 probes (31 min)" in line and " now " not in line


def test_usuality_window_span_is_the_samples_own_not_the_cadence():
    # Two failed probes inside the last ten good ones push the median's window out; the line must say so
    # rather than claim the ten probes covered the usual half hour.
    now = time.time()
    rows = []
    for i in range(200):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (199 - i) * 204))
        ok = not (195 <= i <= 196)
        rows.append({"at": at, "internet": {name: {"ok": ok, "total_ms": 300.0 if ok else None}
                                            for name in ("gstatic", "cloudflare")}})
    line = view.usuality_line(rows, now=now)
    assert "last 10 probes (37 min)" in line


def test_usuality_uses_only_the_last_day_not_all_loaded_rows():
    # 240 rows ten minutes apart span 40 h; only the last day (300 ms) must shape the baseline.
    now = time.time()
    rows = []
    for age_steps in range(240):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - age_steps * 600))
        ms = 100.0 if age_steps >= 144 else 300.0  # older than a day: a misleading fast dip
        rows.append({"at": at, "internet": {name: {"ok": True, "total_ms": ms}
                                            for name in ("gstatic", "cloudflare")}})
    rows.reverse()  # probe.jsonl is oldest-first
    line = view.usuality_line(rows, now=now)
    assert "about usual" in line and "over 24 h" in line
    assert "over 1.7 d" not in line and "much slower" not in line


def test_usuality_recent_spike_is_slower_than_usual():
    rows = _web_rows(200, 300)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["total_ms"] = 450.0  # 1.5x the day's typical
    line = view.usuality_line(rows)
    assert "slower than usual" in line and "about usual" not in line


def test_usuality_big_spike_is_much_slower_than_usual():
    rows = _web_rows(200, 300)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["total_ms"] = 900.0  # 3x the day's typical
    assert "much slower than usual" in view.usuality_line(rows)


def test_usuality_excludes_failed_web_checks_from_the_baseline():
    rows = _web_rows(200, 300)
    for row in rows[:50]:
        for check in row["internet"].values():
            check["ok"] = False
            check["total_ms"] = None
    line = view.usuality_line(rows)
    assert not line.startswith("UNKNOWN") and "300 ms" in line


def test_usuality_marks_a_stale_last_probe():
    rows = _web_rows(200, 300)
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    rows[-1]["at"] = old
    assert "STALE" in view.usuality_line(rows)


def test_day_spark_buckets_the_day_and_handles_empty():
    assert len(view.day_spark([300.0] * 200)) == view.USUAL_BUCKETS
    assert view.day_spark([]) == "(no data)"


def test_probe_cadence_is_measured_not_a_fixed_minute_claim():
    rows = [{"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(i * 180))} for i in range(5)]
    line = view.probe_age_line(rows, 220, 40)
    assert "probes about 3.0 min apart" in line
    assert "one probe a minute" not in line


def test_probe_cadence_follows_a_slow_producer():
    rows = [{"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(i * 600))} for i in range(5)]
    assert "probes about 10.0 min apart" in view.probe_age_line(rows, 700, 40)


def test_probe_cadence_unknown_without_usable_timestamps():
    assert view.probe_cadence_phrase([]) == "probe cadence UNKNOWN"
    assert view.probe_cadence_phrase([{"at": "not-a-time"}]) == "probe cadence UNKNOWN"


def _phase_rows(count, connect, tls, hour, day=10):
    """Probes at a fixed UTC clock hour carrying both phases; the baseline reads the hour from `at` alone."""
    at = f"2026-11-{day:02d}T{hour:02d}:00:00Z"
    return [{"at": at, "internet": {name: {"ok": True, "connect_ms": connect, "tls_ms": tls}
                                    for name in ("gstatic", "cloudflare")}} for _ in range(count)]


def _fresh_phase_row(connect, tls):
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"at": at, "internet": {name: {"ok": True, "connect_ms": connect, "tls_ms": tls}
                                   for name in ("gstatic", "cloudflare")}}


def test_dns_line_explains_why_the_connect_time_below_can_be_smaller():
    line = view.dns_line([{"dns_ms": 73.3}])
    assert "73 ms" in line
    assert "the connect time below includes a lookup; cached answers are faster" in line
    assert len(line) <= 172  # the pane is 172 columns; a wrapped explanation is unreadable


def test_dns_line_is_unknown_when_the_probe_did_not_measure_lookup_time():
    line = view.dns_line([{"dns_ms": None}])
    assert "UNKNOWN" in line
    assert "cached answers are faster" in line


def test_phase_line_splits_reaching_the_site_from_the_handshake():
    line = view.phase_line([_fresh_phase_row(70.0, 900.0)])
    assert "reaching the site 70 ms" in line and "secure handshake 900 ms" in line
    assert "last probe" in line


def test_phase_line_names_both_hosts_when_one_handshake_stalls():
    row = _fresh_phase_row(70.0, 900.0)
    row["internet"]["gstatic"]["tls_ms"] = 299.0
    row["internet"]["cloudflare"]["tls_ms"] = 1262.0
    line = view.phase_line([row])
    assert "secure handshake 780 ms" in line  # keep the mean; the split explains it
    assert "\n                handshakes: gstatic 299 ms, cloudflare 1262 ms (cloudflare slower)" in line


def test_phase_line_names_the_slow_host_in_either_direction():
    row = _fresh_phase_row(70.0, 900.0)
    row["internet"]["gstatic"]["tls_ms"] = 1262.0
    row["internet"]["cloudflare"]["tls_ms"] = 299.0
    assert "(gstatic slower)" in view.phase_line([row])


def test_phase_line_has_no_split_when_hosts_track_each_other():
    assert "handshakes:" not in view.phase_line([_fresh_phase_row(70.0, 300.0)])


def test_phase_line_has_no_split_when_one_host_did_not_answer():
    row = _fresh_phase_row(70.0, 300.0)
    row["internet"]["gstatic"] = {"ok": False, "tls_ms": None}
    line = view.phase_line([row])
    assert not line.startswith("UNKNOWN") and "handshakes:" not in line


def test_phase_line_split_needs_both_a_ratio_and_a_gap():
    row = _fresh_phase_row(70.0, 300.0)
    row["internet"]["gstatic"]["tls_ms"] = 190.0
    row["internet"]["cloudflare"]["tls_ms"] = 210.0  # 1.1x, 20 ms apart
    assert "handshakes:" not in view.phase_line([row])
    row["internet"]["cloudflare"]["tls_ms"] = 385.0  # 2.03x but only 195 ms apart
    assert "handshakes:" not in view.phase_line([row])
    row["internet"]["cloudflare"]["tls_ms"] = 400.0  # 2.1x and 210 ms apart
    assert "handshakes:" in view.phase_line([row])


def test_phase_line_is_unknown_for_a_stale_probe():
    row = _fresh_phase_row(70.0, 900.0)
    row["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    assert view.phase_line([row]).startswith("UNKNOWN")


def test_phase_line_is_unknown_when_the_probe_did_not_measure_the_phases():
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"at": at, "internet": {name: {"ok": True, "total_ms": 300.0}
                                  for name in ("gstatic", "cloudflare")}}
    line = view.phase_line([row])
    assert line.startswith("UNKNOWN") and "did not measure both phases" in line


def test_phase_line_is_unknown_when_both_checks_failed():
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"at": at, "internet": {name: {"ok": False, "connect_ms": None, "tls_ms": None}
                                  for name in ("gstatic", "cloudflare")}}
    assert view.phase_line([row]).startswith("UNKNOWN")


def test_phase_line_is_unknown_without_rows():
    assert view.phase_line([]).startswith("UNKNOWN")


def test_phase_line_reports_the_night_and_evening_handshake_baselines():
    rows = _phase_rows(80, 70.0, 270.0, hour=2) + _phase_rows(80, 70.0, 460.0, hour=19)
    rows.append(_fresh_phase_row(70.0, 300.0))
    line = view.phase_line(rows)
    assert "nights ~270" in line and "evenings ~460" in line
    assert not line.startswith("UNKNOWN")


def test_phase_line_drops_a_baseline_it_cannot_support():
    rows = _phase_rows(10, 70.0, 270.0, hour=2)  # ten night rows, far under PHASE_BASELINE_MIN
    rows.append(_fresh_phase_row(70.0, 300.0))
    line = view.phase_line(rows)
    assert line.startswith("reaching the site")
    assert "nights" not in line and "evenings" not in line
    assert "too few probes" in line


def test_render_frame_shows_the_window_when_a_line_is_fine(monkeypatch):
    monkeypatch.setattr(view, "draw", lambda: "the window")
    assert view.render_frame() == "the window"


def test_render_frame_keeps_the_window_alive_and_loud_on_an_error(monkeypatch):
    # A partly edited view once raised NameError in the SPEED line, the loop died and the outer
    # window vanished; only the five-minute check noticed.  The frame must say it is broken.
    def boom():
        raise NameError("name 'SPEED_BASELINE_NIGHTS' is not defined")
    monkeypatch.setattr(view, "draw", boom)
    frame = view.render_frame()
    assert "WINDOW ERROR" in frame
    assert "NameError: name 'SPEED_BASELINE_NIGHTS' is not defined" in frame
    assert "UNKNOWN" in frame
