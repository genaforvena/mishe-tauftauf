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


def test_live_wifi_line_names_the_weak_device_the_recorded_read_knows(tmp_path, monkeypatch):
    import network_router as nr
    mac = "7C:C3:A1:A5:20:D7"
    _router_log(tmp_path, monkeypatch,
                {"ts": time.time(), "ok": True,
                 "clients": [{"id": nr.client_id(mac), "name": "iMac-Rozalia", "band": "2.4", "dbm": -81}]})
    _wifi_read(monkeypatch, f"BAND 2.472\n{mac}  -81 dBm / unknown (SNR -81)  0 ms ago\nBAND 5.200\n")
    text, weak = view.wifi_facts()
    assert "weak devices: iMac-Rozalia -81 dBm on 2.4 GHz" in text
    assert weak is True


def test_live_wifi_line_keeps_the_bare_dbm_when_no_recorded_name_matches(tmp_path, monkeypatch):
    _router_log(tmp_path, monkeypatch, {"ts": time.time(), "ok": True, "clients": []})
    _wifi_read(monkeypatch, "BAND 2.472\n7C:C3:A1:A5:20:D7  -81 dBm / unknown  0 ms ago\nBAND 5.200\n")
    text, weak = view.wifi_facts()
    assert "weak devices: -81 dBm on 2.4 GHz" in text
    assert weak is True


def test_live_wifi_line_names_every_weak_device_with_its_band(tmp_path, monkeypatch):
    import network_router as nr
    weak24, weak5 = "7C:C3:A1:A5:20:D7", "AA:BB:CC:DD:EE:03"
    _router_log(tmp_path, monkeypatch, {"ts": time.time(), "ok": True, "clients": [
        {"id": nr.client_id(weak24), "name": "iMac-Rozalia", "band": "2.4", "dbm": -81},
        {"id": nr.client_id(weak5), "name": "Redmi-10", "band": "5", "dbm": -78},
    ]})
    _wifi_read(monkeypatch, f"BAND 2.472\n{weak24}  -81 dBm / unknown  0 ms ago\n"
                            f"BAND 5.200\n{weak5}  -78 dBm / unknown  0 ms ago\n")
    text, weak = view.wifi_facts()
    assert "weak devices: iMac-Rozalia -81 dBm on 2.4 GHz, Redmi-10 -78 dBm on 5 GHz" in text
    assert "WEAK SIGNAL" in text and weak is True


def test_live_wifi_line_caps_weak_device_names_and_counts_the_rest(tmp_path, monkeypatch):
    import network_router as nr
    macs = [f"AA:BB:CC:DD:EE:0{n}" for n in range(4)]
    _router_log(tmp_path, monkeypatch, {"ts": time.time(), "ok": True, "clients": [
        {"id": nr.client_id(mac), "name": f"Phone-{n}", "band": "2.4", "dbm": -80 - n}
        for n, mac in enumerate(macs)
    ]})
    _wifi_read(monkeypatch, "BAND 2.472\n" + "".join(
        f"{mac}  {-80 - n} dBm / unknown  0 ms ago\n" for n, mac in enumerate(macs)) + "BAND 5.200\n")
    text, weak = view.wifi_facts()
    assert "weak devices: Phone-3 -83 dBm on 2.4 GHz, Phone-2 -82 dBm on 2.4 GHz, " \
           "Phone-1 -81 dBm on 2.4 GHz (+1 more)" in text, text
    assert weak is True


def test_live_wifi_line_keeps_single_weakest_wording_when_all_devices_are_strong(tmp_path, monkeypatch):
    _router_log(tmp_path, monkeypatch, {"ts": time.time(), "ok": True, "clients": []})
    _wifi_read(monkeypatch, "BAND 2.472\nAA:BB:CC:DD:EE:01  -35 dBm / unknown  0 ms ago\n"
                            "BAND 5.200\nAA:BB:CC:DD:EE:02  -42 dBm / unknown  0 ms ago\n")
    text, weak = view.wifi_facts()
    assert "weakest -42 dBm on 5 GHz" in text and "weak devices" not in text
    assert weak is False and "WEAK SIGNAL" not in text


def test_wifi_line_fits_one_pane_row_even_with_a_long_device_name(monkeypatch):
    # The note beside the line grew a threshold legend once and wrapped the WI-FI row, pushing the
    # frame's title off the pane. Keep the whole row inside the pane's 172 columns.
    long_text = ("6 clients (2.4 GHz 5; 5 GHz 1), weakest A-Very-Long-Household-Device-Name "
                 "-36 dBm on 2.4 GHz; read 41 s ago")
    monkeypatch.setattr(view, "wifi_line", lambda: long_text)
    monkeypatch.setattr(view, "router_facts", lambda: "router up 14.9 days; read 1 s ago")
    line = next(l for l in view.draw().splitlines() if l.startswith("  WI-FI  "))
    assert len(line) <= 172, (len(line), line)


def test_wifi_rows_fit_the_pane_when_several_devices_are_weak(monkeypatch):
    # Naming every weak client makes the listing much longer than the old single-minimum wording.
    # All of it must stay on the pane: a wrapped row pushes the frame's title and code header away.
    long_text = ("7 clients (2.4 GHz 5; 5 GHz 2), weak devices: "
                 "Phone-3 -83 dBm on 2.4 GHz, Phone-2 -82 dBm on 2.4 GHz, "
                 "Phone-1 -81 dBm on 2.4 GHz (+1 more); read 21 s ago  WEAK SIGNAL")
    monkeypatch.setattr(view, "wifi_line", lambda: long_text)
    monkeypatch.setattr(view, "router_facts", lambda: "router up 14.9 days; read 1 s ago")
    drawn = view.draw().splitlines()
    start = next(i for i, line in enumerate(drawn) if line.startswith("  WI-FI  "))
    end = next(i for i in range(start + 1, len(drawn)) if drawn[i].startswith("  WI-FI RADIO"))
    wifi_rows = drawn[start:end]
    assert all(len(row) <= 172 for row in wifi_rows), wifi_rows
    joined = " ".join(wifi_rows)
    assert "Phone-1 -81 dBm on 2.4 GHz" in joined, joined
    assert "read 21 s ago" in joined and "WEAK SIGNAL" in joined, joined


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
    assert view.outage_lines(data) == [
        "  NO PROBES 11-14 16:54Z to 11-18 02:02Z (4869 min): UNKNOWN, not calm",
        "  (over the last 2 probes in 3.4 days (11-14 16:54Z to 11-18 02:02Z))"]


def test_a_probe_silence_of_under_fifteen_minutes_is_not_a_gap():
    data = [_probe("2026-11-10T10:00:00Z", False), _probe("2026-11-10T10:03:00Z", False),
            _probe("2026-11-10T10:17:00Z", False)]
    assert view.probe_gaps(data) == []


def test_probe_silence_is_named_unknown_between_two_calm_probes_and_sorted_with_outages():
    # shape of the 2026-11-24 silence (16:12 -> 17:05Z): one DOWN run, then a long silence to the next row
    data = [_probe("2026-11-10T10:00:00Z", False), _probe("2026-11-10T10:03:00Z", True),
            _probe("2026-11-10T10:06:00Z", False), _probe("2026-11-10T11:00:00Z", False)]
    lines = view.outage_lines(data)
    assert lines[0] == "  DOWN 10:03Z to 10:06Z (3 min)"
    assert lines[1] == "  NO PROBES 11-10 10:06Z to 11-10 11:00Z (54 min): UNKNOWN, not calm"


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


def test_router_facts_shows_the_read_age_and_does_not_freeze_it(monkeypatch):
    # The ROUTER line was the only router-derived reading with no age. A cached read must show its
    # real age, not a frozen "0 s ago" from the instant it was read.
    now = [1_795_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setitem(view._router, "at", 0.0)
    monkeypatch.setitem(view._router, "text", None)
    monkeypatch.setitem(view._router, "read_at", 0.0)
    stdout = "864000\n0.15 0.20 0.25 1/1 1234\n{\"up\":true,\"uptime\":100}\n"
    monkeypatch.setattr(view.subprocess, "run",
                        lambda *a, **k: view.subprocess.CompletedProcess(a, 0, stdout, ""))
    line = view.router_facts()
    assert "router up 10.0 days" in line and "WAN up" in line and "read 0 s ago" in line
    assert "load 0.15 0.20 0.25 (1/5/15 min)" in line  # three bare numbers are not self-explaining
    now[0] += 10  # still inside the 60 s cache: the age must grow with the clock
    line = view.router_facts()
    assert "read 10 s ago" in line and "read 0 s ago" not in line


def test_router_facts_malformed_loadavg_is_unknown_not_a_numberless_line(monkeypatch):
    now = [1_795_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setitem(view._router, "at", 0.0)
    monkeypatch.setitem(view._router, "text", None)
    monkeypatch.setitem(view._router, "read_at", 0.0)
    stdout = "864000\nloadavg unavailable\n{\"up\":true,\"uptime\":100}\n"
    monkeypatch.setattr(view.subprocess, "run",
                        lambda *a, **k: view.subprocess.CompletedProcess(a, 0, stdout, ""))
    assert view.router_facts().startswith("UNKNOWN (ssh read failed)")


def test_router_facts_failed_read_is_unknown_with_age_and_cached(monkeypatch):
    # A dead SSH path must say UNKNOWN with its age (never a calm router line), and the 60 s cache
    # must hold on failure so a dead path is not retried on every redraw.
    now = [1_795_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setitem(view._router, "at", 0.0)
    monkeypatch.setitem(view._router, "text", None)
    monkeypatch.setitem(view._router, "read_at", 0.0)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("ssh down")

    monkeypatch.setattr(view.subprocess, "run", boom)
    line = view.router_facts()
    assert line.startswith("UNKNOWN (ssh read failed)") and "read 0 s ago" in line
    now[0] += 10
    assert view.router_facts().startswith("UNKNOWN")
    assert len(calls) == 1  # cached on failure, not retried every redraw
    now[0] += 55  # past the 60 s cache: one retry, still UNKNOWN
    assert view.router_facts().startswith("UNKNOWN")
    assert len(calls) == 2


def test_gateway_line_shows_jitter_and_unknown_when_missing():
    rows = [{"gateway": {"loss_pct": 0.0, "avg_ms": 0.47, "jitter_ms": 0.027}}]
    line = view.gateway_line(rows)
    assert "jitter 0.03 ms" in line
    old = [{"gateway": {"loss_pct": 0.0, "avg_ms": 0.47}}]
    assert "jitter UNKNOWN" in view.gateway_line(old)
    assert "jitter UNKNOWN" in view.gateway_line([{"gateway": None}])
    assert "jitter UNKNOWN" in view.gateway_line([])


def test_gateway_worst_loss_names_loaded_span_not_the_sparkline():
    rows = [{"at": f"2026-11-18T{h:02d}:00:00Z", "gateway": {"avg_ms": 0.5, "loss_pct": 0.0}}
            for h in range(24)]
    line = view.gateway_line(rows)
    assert "worst loss 0% (over 23.0 h)" in line


def test_gateway_worst_loss_keeps_blip_outside_sparkline_and_labels_it():
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 11, 17, tzinfo=timezone.utc)
    rows = [{"at": "2026-11-16T00:00:00Z", "gateway": {"avg_ms": 0.5, "loss_pct": 12.0}}]
    rows += [{"at": (base + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "gateway": {"avg_ms": 0.5, "loss_pct": 0.0}} for h in range(48)]
    line = view.gateway_line(rows)
    assert "worst loss 12% (over 3.0 days)" in line


def test_gateway_worst_loss_without_timestamps_invents_no_span():
    line = view.gateway_line([{"gateway": {"avg_ms": 0.5, "loss_pct": 3.0}}])
    assert "worst loss 3%" in line and "(over" not in line


def test_gateway_worst_loss_survives_a_malformed_timestamp_without_a_span():
    rows = [{"at": "not-a-time", "gateway": {"avg_ms": 0.5, "loss_pct": 3.0}},
            {"at": "2026-11-18T01:00:00Z", "gateway": {"avg_ms": 0.5, "loss_pct": 0.0}}]
    line = view.gateway_line(rows)
    assert "worst loss 3%" in line and "(over" not in line


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
    line = view.uplink_rate_line(now)
    assert "300 receive drops in the last 50 min (about 360 per hour)" in line
    assert "read 0 s ago" in line and "STALE" not in line


def test_uplink_rate_restarts_after_a_reboot(tmp_path, monkeypatch):
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, _uplink_read(now - 3000, 90000, 5000))
    log = tmp_path / "router.jsonl"
    log.write_text(log.read_text() + json.dumps(_uplink_read(now - 2000, 1000, 10)) + "\n"
                   + json.dumps(_uplink_read(now, 3000, 40)) + "\n")
    # after the reboot the span is 2000 s with 30 drops: the pre-reboot counter is not subtracted
    assert "30 receive drops" in view.uplink_rate_line(now)


def test_uplink_rate_marks_a_dead_producer_as_stale(tmp_path, monkeypatch):
    # The two reads still bracket ten minutes of counters an hour later, but the newest is 20 min old:
    # a dead producer must not leave a calm trend beside lines that say STALE.
    now = 1_000_000.0
    _router_log(tmp_path, monkeypatch, [_uplink_read(now - 1800, 100000, 1000),
                                        _uplink_read(now - 1200, 100600, 1300)])
    line = view.uplink_rate_line(now)
    assert "300 receive drops in the last 10 min" in line
    assert "read 1200 s ago  STALE" in line


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


def test_router_health_says_when_the_busy_hold_ends(tmp_path, monkeypatch):
    now = 1_000_000.0
    real = {"ts": now - 300, "ok": True, "clients": [], "router_load": {"load_5m": 1.9, "mem_available_mb": 226.0}}
    skip = {"ts": now, "ok": False, "error": "skipped: router over its self-limit at the last read"}
    _router_log(tmp_path, monkeypatch, real)
    with open(view.ROUTER_LOG, "a") as fh:
        fh.write(json.dumps(skip) + "\n")
    line = view.router_health_line(now)
    due = time.strftime("%H:%M", time.gmtime(now - 300 + view.network_router.COOLDOWN_S))
    assert line.startswith("UNKNOWN (router busy") and f"until about {due} UTC" in line
    assert "skipped: router over its self-limit" in line


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


def _web_rows(n, ms, ok=True, now=None):
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))
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
    assert "about usual" in line and "14 h of samples" in line
    assert "trend 24 h:" in line
    assert "over 1.7 d" not in line and "much slower" not in line


def test_usuality_span_is_labeled_as_samples_not_the_slowdown_duration():
    # The headline already says the slowdown lasted N min. A bare "over 24 h" in USUAL read as the
    # slowdown lasting a day, contradicting the line above it; that span is the sample window.
    now = time.time()
    rows = []
    for i in range(200):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (199 - i) * 204))
        ms = 900.0 if i >= 190 else 300.0
        rows.append({"at": at, "internet": {name: {"ok": True, "total_ms": ms}
                                            for name in ("gstatic", "cloudflare")}})
    line = view.usuality_line(rows, now=now)
    assert f"{view.usuality_facts(rows, now)['base_span']} of samples" in line
    assert "slower than usual, over" not in line
    assert "much slower than usual over 24 h" not in line
    assert "over the last 31 min" in view.headline_state("OK", rows, now=now)


def test_usuality_baseline_label_names_the_baselines_own_span_not_the_loaded_day():
    # The typical drops the newest 60 good samples so a spike cannot inflate it. A 24 h day at the
    # real 204 s cadence leaves a baseline of about 21 h; the old label claimed the whole day.
    now = time.time()
    rows = _timed_phase_rows(423, now, 204, 40.0, 70.0)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["tls_ms"] = 200.0
            check["total_ms"] = 270.0

    facts = view.usuality_facts(rows, now=now)
    assert facts["span"] == "24 h" and facts["base_span"] == "21 h"
    usual = view.usuality_line(rows, now=now, facts=facts)
    phase = view.phase_line(rows, now=now, facts=facts)
    assert "21 h of samples" in usual and "24 h of samples" not in usual
    assert "trend 24 h:" in usual
    assert "21 h of samples" in phase and "24 h of samples" not in phase
    assert all(len(part) <= 172 for part in usual.split("\n"))
    assert all(len(part) <= 172 for part in phase.split("\n"))


def test_usuality_baseline_label_follows_a_web_gap_not_the_loaded_day():
    # Twelve hours of failed web checks leave the baseline covering only the good hours; its label
    # must follow those rows rather than the loaded window.
    now = time.time()
    rows = []
    for i in range(288):  # 300 s apart, a full day
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (287 - i) * 300))
        ok = i >= 144
        slow = ok and i >= 278
        chk = {"ok": ok, "connect_ms": 40.0, "tls_ms": 200.0 if slow else 70.0,
               "total_ms": 270.0 if slow else 140.0}
        rows.append({"at": at, "internet": {name: dict(chk) for name in ("gstatic", "cloudflare")}})
    facts = view.usuality_facts(rows, now=now)
    assert facts["span"] == "24 h" and facts["base_span"] == "7 h"
    usual = view.usuality_line(rows, now=now, facts=facts)
    phase = view.phase_line(rows, now=now, facts=facts)
    assert "7 h of samples" in usual and "24 h of samples" not in usual
    assert "7 h of samples" in phase and "24 h of samples" not in phase


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


def _evening_rows(now, evening_ms, tonight_ms, other_ms=300.0):
    """Three days of rows at the real cadence ending inside the evening phase: earlier evenings at
    `evening_ms`, tonight's evening at `tonight_ms`, every other hour at `other_ms`."""
    rows = []
    for i in range(3 * 424):
        t = now - (3 * 424 - 1 - i) * 204
        hour = time.gmtime(t).tm_hour
        tonight = hour in range(18, 21) and (now - t) < 86400
        ms = tonight_ms if tonight else (evening_ms if hour in range(18, 21) else other_ms)
        rows.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
                     "internet": {name: {"ok": True, "total_ms": ms}
                                  for name in ("gstatic", "cloudflare")}})
    return rows


def test_ordinary_evening_is_about_usual_for_the_evening(monkeypatch):
    # Evenings run structurally elevated (the retained rows' evening median is about 1.3x the
    # pooled day median), so the pooled baseline read "slower than usual" on every ordinary
    # evening. The typical must come from the evenings' own samples. The clock is frozen to the
    # rows' own moment, or `age` reads them a year stale.
    now = 1_795_113_900  # 2025-11-19T19:30:00Z, inside the evening phase
    monkeypatch.setattr(time, "time", lambda: now)
    rows = _evening_rows(now, evening_ms=480.0, tonight_ms=500.0)
    line = view.usuality_line(rows, now=now)
    assert "typical 480 ms" in line and "of evenings" in line and "about usual" in line
    assert "slower than usual" not in line
    assert view.headline_state("OK", rows, now=now) == "OK"


def test_slow_evening_flags_against_the_evenings_own_baseline(monkeypatch):
    # A genuinely slow evening must still flag, and tonight's own slow samples must not calibrate
    # the baseline they are judged against: the pool is earlier evenings.
    now = 1_795_113_900  # 2025-11-19T19:30:00Z, inside the evening phase
    monkeypatch.setattr(time, "time", lambda: now)
    rows = _evening_rows(now, evening_ms=480.0, tonight_ms=1300.0)
    line = view.usuality_line(rows, now=now)
    assert "typical 480 ms" in line and "of evenings" in line and "much slower than usual" in line
    assert "for the evening" in view.headline_state("OK", rows, now=now)


def test_thin_evening_support_falls_back_to_the_day_comparison(monkeypatch):
    # One day of rows holds only about three hours of evening samples; with no evening baseline
    # the comparison falls back to the pooled day median, labelled "of samples", and the headline
    # does not claim an evening it has no samples for.
    now = 1_795_113_900  # 2025-11-19T19:30:00Z, inside the evening phase
    monkeypatch.setattr(time, "time", lambda: now)
    rows = _evening_rows(now, evening_ms=450.0, tonight_ms=450.0)[-424:]  # the last day only
    facts = view.usuality_facts(rows, now=now)
    line = view.usuality_line(rows, now=now, facts=facts)
    assert facts["clock_base"] is None
    assert "of samples" in line and "of evenings" not in line
    assert "for the evening" not in view.headline_state("OK", rows, now=now, facts=facts)


def _busy_window_rows(now, busy_ms, tonight_ms, other_ms=300.0):
    """Three days at the real cadence ending inside the 16-20h busy window, independent of the
    view's phase constant: earlier busy hours at `busy_ms`, tonight's busy hours at `tonight_ms`,
    every other hour at `other_ms`. Building the data from the measured peak keeps this a test of
    the constant, not a restatement of it.
    """
    rows = []
    for i in range(3 * 424):
        t = now - (3 * 424 - 1 - i) * 204
        hour = time.gmtime(t).tm_hour
        busy = 16 <= hour <= 20
        tonight = busy and (now - t) < 86400
        ms = tonight_ms if tonight else (busy_ms if busy else other_ms)
        rows.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
                     "internet": {name: {"ok": True, "total_ms": ms}
                                  for name in ("gstatic", "cloudflare")}})
    return rows


def test_busy_peak_hours_get_the_busy_baseline_not_the_day(monkeypatch):
    # The retained day's web-latency peak is 16-18h UTC (609/671/562 ms), not 18-20h. With an
    # 18-20h-only phase an ordinary 17h was judged against the quiet day median and read
    # "slower than usual" every day at the busiest hour. The phase must cover the peak hours.
    now = 1_795_106_700  # 2025-11-19T17:30:00Z, inside the 16-20h busy window
    monkeypatch.setattr(time, "time", lambda: now)
    rows = _busy_window_rows(now, busy_ms=650.0, tonight_ms=680.0)
    assert "of evenings" in view.usuality_line(rows, now=now)
    assert view.headline_state("OK", rows, now=now) == "OK"


def test_slow_peak_hour_still_flags_against_the_busy_baseline(monkeypatch):
    # Widening the phase must not silence a genuine slowdown at the peak: tonight's own slow samples
    # still flag against the earlier busy hours, and the headline says "for the evening".
    now = 1_795_106_700  # 2025-11-19T17:30:00Z
    monkeypatch.setattr(time, "time", lambda: now)
    rows = _busy_window_rows(now, busy_ms=650.0, tonight_ms=1400.0)
    line = view.usuality_line(rows, now=now)
    assert "typical 650 ms" in line and "of evenings" in line and "much slower than usual" in line
    assert "for the evening" in view.headline_state("OK", rows, now=now)


def test_non_evening_hours_keep_the_pooled_typical():
    # Outside the evening phase the pooled wording stays as it was.
    noon = 1_795_003_200  # 2025-11-18T12:00:00Z
    line = view.usuality_line(_web_rows(200, 300, now=noon), now=noon)
    assert "vs typical 300 ms" in line and "evening" not in line


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


def test_headline_names_a_sustained_slowdown_without_changing_the_verdict(monkeypatch):
    # Pinned outside the evening hours and with the clock frozen: during them the typical is the
    # evening's own and the headline adds "for the evening" (its own test below). `age` reads the
    # real clock, so it must be frozen to the same pinned moment or every row reads stale.
    noon = 1_795_003_200  # 2025-11-18T12:00:00Z
    monkeypatch.setattr(time, "time", lambda: noon)
    rows = _web_rows(200, 300, now=noon)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["total_ms"] = 900.0  # 3x the day's typical, as the USUAL line would see it
    assert view.headline_state("OK", rows, now=noon) == "OK, but much slower than usual over the last 10 probes"
    assert "much slower than usual" in view.usuality_line(rows, now=noon)


def test_headline_uses_the_probes_own_times_for_the_window():
    now = time.time()
    rows = []
    for i in range(200):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (199 - i) * 204))
        ms = 900.0 if i >= 190 else 300.0
        rows.append({"at": at, "internet": {name: {"ok": True, "total_ms": ms}
                                            for name in ("gstatic", "cloudflare")}})
    assert "much slower than usual over the last 31 min" in view.headline_state("OK", rows, now=now)


def test_headline_leaves_down_unknown_short_and_stale_states_alone():
    rows = _web_rows(200, 300)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["total_ms"] = 900.0
    assert view.headline_state("DOWN: router answers", rows) == "DOWN: router answers"
    assert view.headline_state("UNKNOWN (no fresh probe)", rows) == "UNKNOWN (no fresh probe)"
    assert view.headline_state("OK", _web_rows(10, 300)) == "OK"  # no baseline yet
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    rows[-1]["at"] = old
    assert view.headline_state("OK", rows) == "OK"  # stale is never a calm claim about slowness


def test_day_spark_buckets_the_day_and_handles_empty():
    assert len(view.day_spark([300.0] * 200)) == view.USUAL_BUCKETS
    assert view.day_spark([]) == "(no data)"


def test_day_spark_draws_a_dead_stretch_as_a_gap_not_skipped():
    # A middle stretch of failed checks must keep its place on the trend, not close up.
    line = view.day_spark([300.0] * 100 + [None] * 100 + [600.0] * 200)
    assert len(line) == view.USUAL_BUCKETS
    assert "·" in line and line.index("·") > 0 and line.rindex("·") < len(line) - 1
    assert view.day_spark([None] * 50) == "(no data)"


def test_probe_cadence_is_measured_not_a_fixed_minute_claim():
    rows = [{"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(i * 180))} for i in range(5)]
    line = view.probe_age_line(rows, 220, 40)
    assert "probes about 3.0 min apart" in line
    assert "one probe a minute" not in line


def test_usual_trend_names_its_day_window_not_the_default_40_probes():
    # The header's default is "last 40 probes"; day_spark buckets a whole day of samples into 40
    # means. Without its own label a day-long latency ramp reads as a two-hour ramp.
    now = time.time()
    rows = []
    for i in range(200):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (199 - i) * 204))
        rows.append({"at": at, "internet": {name: {"ok": True, "total_ms": 300.0}
                                            for name in ("gstatic", "cloudflare")}})
    line = view.usuality_line(rows, now=now)
    assert f"trend {view.usuality_facts(rows, now)['span']}:" in line
    assert "trend: " not in line


def test_header_says_a_wider_sparkline_window_is_labelled():
    rows = [{"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(i * 180))} for i in range(5)]
    line = view.probe_age_line(rows, 220, 40)
    assert "sparklines = last 40 probes unless labelled" in line


def test_probe_cadence_follows_a_slow_producer():
    rows = [{"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(i * 600))} for i in range(5)]
    assert "probes about 10.0 min apart" in view.probe_age_line(rows, 700, 40)


def test_probe_cadence_unknown_without_usable_timestamps():
    assert view.probe_cadence_phrase([]) == "probe cadence UNKNOWN"
    assert view.probe_cadence_phrase([{"at": "not-a-time"}]) == "probe cadence UNKNOWN"


def test_probe_header_marks_a_dead_producer_stale_not_just_old():
    # The per-site rows keep their last numbers and "failed in last 40: 0" after the producer dies;
    # the header is the only line governing all of them, so it must say STALE, not only count seconds.
    rows = [{"at": "2026-11-20T01:00:00Z"}] * 5
    fresh = view.probe_age_line(rows, 20, 40)
    stale = view.probe_age_line(rows, view.STALE_S + 60, 40)
    assert "STALE" not in fresh
    assert "last probe: 360s ago" in stale
    assert "STALE" in stale


def test_probe_header_with_no_timestamp_is_unknown_not_stale_calm():
    line = view.probe_age_line([{"at": "not-a-time"}], None, 40)
    assert "last probe: never" in line
    assert "STALE" not in line


def _phase_rows(count, connect, tls, hour, day=10):
    """Probes at a fixed UTC clock hour carrying all three phases; baselines read the hour from `at`."""
    at = f"2026-11-{day:02d}T{hour:02d}:00:00Z"
    return [{"at": at, "internet": {name: {"ok": True, "connect_ms": connect, "tls_ms": tls,
                                            "total_ms": tls + 72.0}
                                    for name in ("gstatic", "cloudflare")}} for _ in range(count)]


def _fresh_phase_row(connect, tls):
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"at": at, "internet": {name: {"ok": True, "connect_ms": connect, "tls_ms": tls,
                                          "total_ms": tls + 72.0}
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
    assert "reaching the site 70 ms" in line and "secure handshake 830 ms" in line
    assert "rest of page 72 ms" in line
    assert "last probe" in line


def test_phase_line_names_both_hosts_when_one_handshake_stalls():
    row = _fresh_phase_row(70.0, 900.0)
    row["internet"]["gstatic"]["tls_ms"] = 299.0
    row["internet"]["gstatic"]["total_ms"] = 371.0
    row["internet"]["cloudflare"]["tls_ms"] = 1262.0
    row["internet"]["cloudflare"]["total_ms"] = 1334.0
    line = view.phase_line([row])
    assert "secure handshake 710 ms" in line  # keep the mean; the split explains it
    assert "\n                handshakes: gstatic 229 ms, cloudflare 1192 ms (cloudflare slower)" in line


def test_phase_line_names_the_slow_host_in_either_direction():
    row = _fresh_phase_row(70.0, 900.0)
    row["internet"]["gstatic"]["tls_ms"] = 1262.0
    row["internet"]["gstatic"]["total_ms"] = 1334.0
    row["internet"]["cloudflare"]["tls_ms"] = 299.0
    row["internet"]["cloudflare"]["total_ms"] = 371.0
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
    row["internet"]["gstatic"]["total_ms"] = 262.0
    row["internet"]["cloudflare"]["tls_ms"] = 210.0  # 1.1x, 20 ms apart
    row["internet"]["cloudflare"]["total_ms"] = 282.0
    assert "handshakes:" not in view.phase_line([row])
    row["internet"]["cloudflare"]["tls_ms"] = 385.0  # 2.03x but only 195 ms apart
    row["internet"]["cloudflare"]["total_ms"] = 457.0
    assert "handshakes:" not in view.phase_line([row])
    row["internet"]["cloudflare"]["tls_ms"] = 400.0  # 2.1x and 210 ms apart
    row["internet"]["cloudflare"]["total_ms"] = 472.0
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
    assert line.startswith("UNKNOWN") and "did not measure all three phases" in line


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
    assert "nights ~200" in line and "evenings ~390" in line
    assert not line.startswith("UNKNOWN")


def test_phase_line_phases_sum_to_the_page_total():
    line = view.phase_line([_fresh_phase_row(70.0, 900.0)])
    assert "reaching the site 70 ms, secure handshake 830 ms, rest of page 72 ms" in line
    assert "secure handshake 900 ms" not in line  # curl's cumulative appconnect is not a phase


def test_phase_line_is_unknown_when_a_checkpoint_is_reversed():
    row = _fresh_phase_row(70.0, 900.0)
    row["internet"]["gstatic"]["total_ms"] = 800.0  # before the handshake finished
    row["internet"]["cloudflare"]["total_ms"] = 800.0
    assert view.phase_line([row]).startswith("UNKNOWN")


def test_phase_line_drops_a_baseline_it_cannot_support():
    rows = _phase_rows(10, 70.0, 270.0, hour=2)  # ten night rows, far under PHASE_BASELINE_MIN
    rows.append(_fresh_phase_row(70.0, 300.0))
    line = view.phase_line(rows)
    assert line.startswith("reaching the site")
    assert "nights" not in line and "evenings" not in line
    assert "too few probes" in line


def _timed_phase_rows(n, now, step, connect, tls, body=70.0):
    """Rows spaced `step` seconds ending at `now`, each with all three curl checkpoints."""
    rows = []
    for i in range(n):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (n - 1 - i) * step))
        rows.append({"at": at, "internet": {name: {"ok": True, "connect_ms": connect,
                                                   "tls_ms": tls, "total_ms": tls + body}
                                            for name in ("gstatic", "cloudflare")}})
    return rows


def test_phase_line_names_the_phase_behind_a_sustained_slowdown():
    # The newest probe can be ordinary while the ten-probe median stays slow; the phase line must
    # use the same window and baseline as USUAL so the reader can see what moved.
    now = time.time()
    rows = _timed_phase_rows(200, now, 204, 70.0, 290.0)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["tls_ms"] = 445.0  # handshake 375 ms instead of the usual 220 ms
            check["total_ms"] = 515.0
    line = view.phase_line(rows, now=now)
    facts = view.usuality_facts(rows, now=now)
    assert "slowdown is in the secure handshake: last 10 probes (31 min) 375 ms" in line
    # The typical is the day's baseline, not the ten-probe recent window beside it; both lines must
    # say so, or a reader carries "31 min" onto the typical figures.
    assert f"vs typical 220 ms ({facts['base_span']} of samples)" in line
    assert "median 515 ms vs typical 360 ms" in view.usuality_line(rows, now=now, facts=facts)
    assert all(len(part) <= 172 for part in line.split("\n"))  # the pane is 172 columns


def test_phase_line_has_no_slowdown_note_when_the_page_is_usual():
    now = time.time()
    line = view.phase_line(_timed_phase_rows(200, now, 204, 70.0, 290.0), now=now)
    assert "slowdown is in" not in line


def test_phase_line_has_no_slowdown_note_when_no_phase_dominates():
    now = time.time()
    rows = _timed_phase_rows(200, now, 204, 70.0, 290.0)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["connect_ms"] = 100.0   # +30 ms reaching
            check["tls_ms"] = 350.0       # handshake 250 ms instead of 220 (+30)
            check["total_ms"] = 450.0     # +30 ms reply
    assert "slower than usual" in view.usuality_line(rows, now=now)
    assert "slowdown is in" not in view.phase_line(rows, now=now)


def test_phase_line_has_no_slowdown_note_without_measured_phases():
    rows = _web_rows(200, 300)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["total_ms"] = 900.0
    line = view.phase_line(rows)
    assert line.startswith("UNKNOWN") and "slowdown is in" not in line


def _total_only_row(row, total_ms):
    """Keep a web sample but remove every phase checkpoint from a probe row."""
    for check in row["internet"].values():
        check.pop("connect_ms", None)
        check.pop("tls_ms", None)
        check["total_ms"] = total_ms


def test_phase_line_reports_unknown_support_when_one_recent_probe_has_phases():
    # A field review reproduced a claim of a ten-probe handshake median from one measured probe.
    # USUAL can still flag the slowdown; the attribution must not pretend to ten observations.
    now = time.time()
    rows = _timed_phase_rows(200, now, 204, 70.0, 290.0)
    for row in rows[-10:-1]:
        _total_only_row(row, 700.0)
    for check in rows[-1]["internet"].values():
        check["tls_ms"] = 600.0
        check["total_ms"] = 700.0
    facts = view.usuality_facts(rows, now=now)
    assert facts["phase_support"] == 1
    assert "slower than usual" in view.usuality_line(rows, now=now, facts=facts)
    line = view.phase_line(rows, now=now, facts=facts)
    assert "slowdown is in" not in line
    assert ("slowdown phase UNKNOWN (phases measured on 1 of 10 recent probes, "
            "140 baseline rows)") in line
    assert all(len(part) <= 172 for part in line.split("\n"))


def test_phase_line_reports_unknown_support_when_the_baseline_has_no_phases():
    now = time.time()
    rows = _timed_phase_rows(200, now, 204, 70.0, 290.0)
    for row in rows[:-10]:
        _total_only_row(row, 360.0)
    for row in rows[-10:]:
        for check in row["internet"].values():
            check["tls_ms"] = 600.0
            check["total_ms"] = 700.0
    facts = view.usuality_facts(rows, now=now)
    assert facts["phase_support"] == 10 and facts["phase_base_support"] == 0
    line = view.phase_line(rows, now=now, facts=facts)
    assert "slowdown is in" not in line
    assert "slowdown phase UNKNOWN (phases measured on 10 of 10 recent probes, 0 baseline rows)" in line


def test_phase_line_names_partial_support_and_compares_the_measured_rows_only():
    # Half the window has phases; the others only have a web total. The attribution's extra time
    # must come from the measured rows, not a median mixed with the total-only ones.
    now = time.time()
    rows = _timed_phase_rows(200, now, 204, 70.0, 290.0)
    sparse = set(id(row) for row in rows[-10::2])
    for row in rows[-10:]:
        if id(row) in sparse:
            _total_only_row(row, 1000.0)
        else:
            for check in row["internet"].values():
                check["tls_ms"] = 445.0
                check["total_ms"] = 515.0
    facts = view.usuality_facts(rows, now=now)
    assert facts["phase_support"] == 5
    assert "median 758 ms vs typical 360 ms" in view.usuality_line(rows, now=now, facts=facts)
    line = view.phase_line(rows, now=now, facts=facts)
    assert ("slowdown is in the secure handshake: 5 of last 10 probes (27 min) measured "
            f"375 ms vs typical 220 ms ({facts['base_span']} of samples)") in line
    assert "758 ms" not in line


def test_slowdown_phase_recent_span_names_only_the_measured_rows():
    # Phase checkpoints on only part of the recent window: the parenthetical minutes must span the
    # rows that supplied the median, not every web-good probe. Five newest probes span ~14 min;
    # all ten web-good probes span ~31 min, which would overstate the measured support.
    now = time.time()
    rows = _timed_phase_rows(200, now, 204, 70.0, 290.0)
    for row in rows[-10:-5]:
        _total_only_row(row, 1000.0)
    for row in rows[-5:]:
        for check in row["internet"].values():
            check["tls_ms"] = 445.0
            check["total_ms"] = 515.0
    facts = view.usuality_facts(rows, now=now)
    assert facts["window_count"] == 10 and facts["phase_support"] == 5
    assert facts["window"] == "last 10 probes (31 min)"
    line = view.phase_line(rows, now=now, facts=facts)
    assert "slowdown is in the secure handshake: 5 of last 10 probes (14 min) measured" in line
    assert "(31 min)" not in line
    assert all(len(part) <= 172 for part in line.split("\n"))


def test_slowdown_phase_typical_names_its_own_rows_span_not_the_web_baseline():
    # The phase typical is built only from rows where a host measured all three curl checkpoints.
    # Labelling it with the web baseline's span claims hours the phase rows never covered.
    now = time.time()
    n, step = 423, 204
    phase_baseline = set(range(263, 363))  # 100 rows ~ 5.6 h, not the web baseline's ~21 h
    rows = []
    for i in range(n):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (n - 1 - i) * step))
        slow = i >= 413
        full = i in phase_baseline or slow
        chk = {"ok": True, "total_ms": 700.0 if slow else 340.0}
        if full:
            chk["connect_ms"] = 40.0
            chk["tls_ms"] = 470.0 if slow else 270.0
        rows.append({"at": at, "internet": {name: dict(chk) for name in ("gstatic", "cloudflare")}})
    facts = view.usuality_facts(rows, now=now)
    assert facts["base_span"] == "21 h" and facts["phase_base_span"] == "6 h"
    assert facts["phase_base_support"] == 100
    usual = view.usuality_line(rows, now=now, facts=facts)
    line = view.phase_line(rows, now=now, facts=facts)
    assert "slowdown is in the secure handshake" in line
    assert "vs typical 230 ms (6 h of samples)" in line
    assert "21 h of samples" not in line
    assert "21 h of samples" in usual
    assert all(len(part) <= 172 for part in line.split("\n"))


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


def test_spike_row_counts_the_probes_the_median_hides():
    # Three of the last ten web probes are over a second while the median sits near the typical.
    recent = [370.0, 1117.0, 379.0, 364.0, 413.0, 392.0, 1182.0, 366.0, 375.0, 1055.0]
    line = view.spike_share_line({"recent_good": recent})
    assert line.startswith("  SPIKES")
    assert "3 of the last 10 web probes over 1 s" in line
    assert len(line) <= 172


def test_spike_row_is_unknown_without_web_samples():
    assert "UNKNOWN" in view.spike_share_line({})


def test_spike_row_counts_samples_when_the_baseline_is_too_thin():
    # Fewer than USUAL_MIN_SAMPLES web samples: no USUAL comparison, but the SPIKES row must still
    # count the samples it has instead of claiming there are none.
    import time as _time
    now = 1_800_000_000.0
    data = []
    for i, ms in enumerate([370, 1117, 379, 364, 413, 392, 1182, 366, 375, 1055]):
        stamp = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now - (10 - i) * 300))
        check = {"ok": True, "total_ms": ms}
        data.append({"at": stamp, "internet": {"gstatic": check, "cloudflare": check}})
    facts = view.usuality_facts(data, now=now)
    assert facts["compare"] is None
    line = view.spike_share_line(facts)
    assert "UNKNOWN" not in line
    assert "3 of the last 10 web probes over 1 s" in line


def test_a_gap_during_a_containment_pause_names_the_trip_not_a_bare_unknown():
    # shape of the 2026-11-24 silence (16:12 -> 17:05Z): the producers were skipped by the guard
    data = [_probe("2026-11-24T16:12:12Z", False), _probe("2026-11-24T17:05:48Z", False)]
    a = view.nb.utc_epoch("2026-11-24T16:12:12Z")
    b = view.nb.utc_epoch("2026-11-24T17:05:48Z")
    pause = (view.nb.utc_epoch("2026-11-24T16:14:02Z"), view.nb.utc_epoch("2026-11-24T17:05:48Z"))
    lines = view.outage_lines(data, paused=lambda x, y: pause if (x, y) == (a, b) else None)
    assert lines[0] == ("  NO PROBES 11-24 16:12Z to 11-24 17:05Z (54 min): host paused by containment "
                        "trip 16:14Z to 17:05Z, UNKNOWN not calm")


def test_a_gap_with_no_pause_on_record_stays_plain_unknown():
    data = [_probe("2026-11-24T16:12:12Z", False), _probe("2026-11-24T17:05:48Z", False)]
    assert view.outage_lines(data)[0].endswith("UNKNOWN, not calm")
    assert view.outage_lines(data, paused=lambda x, y: None)[0].endswith("UNKNOWN, not calm")


def test_containment_pause_reads_the_skip_lines_of_the_network_producers(monkeypatch):
    journal = "\n".join([
        "2026-11-24T16:14:02+00:00 mesh-home systemd[1116]: mishe-arm.service - Re-arm was skipped "
        "because of an unmet condition check (ConditionPathExists=!/var/lib/mishe-containment/tripped).",
        "2026-11-24T16:14:04+00:00 mesh-home systemd[1116]: mishe-producer-network-probe.service - "
        "Mishe producer network-probe was skipped because of an unmet condition check "
        "(ConditionPathExists=!/var/lib/mishe-containment/tripped).",
        "2026-11-24T16:40:00+00:00 mesh-home systemd[1116]: mishe-producer-network-devices.service - "
        "skipped because of an unmet condition check (ConditionPathExists=!/var/lib/mishe-containment/tripped).",
        "2026-11-24T19:00:00+00:00 mesh-home systemd[1116]: mishe-producer-network-probe.service - "
        "skipped because of an unmet condition check (ConditionPathExists=!/var/lib/mishe-containment/tripped).",
    ])

    class Run:
        returncode = 0
        stdout = journal

    view._pause_cache.clear()
    monkeypatch.setattr(view.subprocess, "run", lambda *args, **kwargs: Run())
    a = view.nb.utc_epoch("2026-11-24T16:12:12Z")
    b = view.nb.utc_epoch("2026-11-24T17:05:48Z")
    start, end = view.containment_pause(a, b)
    assert (view.hhmm(start), view.hhmm(end)) == ("16:14", "16:40")


def test_a_failed_journal_read_is_no_pause_so_the_gap_stays_unknown(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no journalctl")

    view._pause_cache.clear()
    monkeypatch.setattr(view.subprocess, "run", boom)
    assert view.containment_pause(1795536000, 1795540000) is None
