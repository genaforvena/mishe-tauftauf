"""Wi-Fi channel and noise: decoded from read-only iwinfo lines, UNKNOWN/STALE in the window."""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


boundary = load(REPO / "tools" / "network_boundary.py", "network_boundary_wifi_radio_under_test")
view = load(REPO / "tools" / "network_view.py", "network_view_wifi_radio_under_test")

INFO_24 = ["  Mode: Master  Channel: 13 (2.472 GHz) HT Mode: HE20",
           "  Signal: -25 dBm  Noise: -56 dBm"]
INFO_5 = ["  Mode: Master  Channel: 40 (5.200 GHz) HT Mode: HE80",
          "  Signal: -31 dBm  Noise: -56 dBm"]


def test_decode_channel_frequency_and_noise():
    assert boundary.decode_wifi_radio(INFO_24) == {"channel": 13, "freq_ghz": 2.472, "noise_dbm": -56}


def test_missing_fields_stay_unknown():
    assert boundary.decode_wifi_radio([]) is None
    assert boundary.decode_wifi_radio(["  Mode: Master  Channel: unknown"]) is None
    info = boundary.decode_wifi_radio(["  Mode: Master  Channel: 13 (2.472 GHz)"])
    assert info == {"channel": 13, "freq_ghz": 2.472, "noise_dbm": None}


def test_router_row_carries_both_radios_without_changing_clients():
    stdout = "\n".join([
        "UPTIME 958323.10",
        "RADIO ra0", *INFO_24,
        "AA:BB:CC:00:00:01  -29 dBm / unknown (SNR -29)  0 ms ago",
        "\tRX: 39.0 MBit/s, MCS 4",
        "RADIO rax0", *INFO_5,
        "78:8A:86:FC:48:16  -32 dBm / unknown (SNR -32)  0 ms ago",
        "LEASES", "WAN", '{"up": true, "l3_device": "eth0"}', "LINKS",
    ])
    body = boundary.decode_router_output(stdout, {"ra0": "2.4", "rax0": "5"}, lambda mac: "x")
    assert body["radios"] == [
        {"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -56, "stats": None},
        {"band": "5", "channel": 40, "freq_ghz": 5.2, "noise_dbm": -56, "stats": None},
    ]
    assert len(body["clients"]) == 2 and body["clients"][0]["rx"] == 39.0


def test_view_line_names_both_bands_with_age(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    row = {"ts": 1000.0, "ok": True, "radios": [
        {"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -56},
        {"band": "5", "channel": 40, "freq_ghz": 5.2, "noise_dbm": -60},
    ]}
    log.write_text(json.dumps(row) + "\n")
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.wifi_radio_line(now=1060.0)
    assert "2.4 GHz ch 13 (2.472 GHz), noise -56 dBm" in line
    assert "5 GHz ch 40 (5.2 GHz), noise -60 dBm" in line
    assert "read 60 s ago" in line and "STALE" not in line


def test_view_line_is_unknown_without_radio_reading(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000.0, "ok": true}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert view.wifi_radio_line(now=1060.0).startswith("UNKNOWN")


def test_view_line_marks_old_reading_stale(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000.0, "ok": true, "radios": '
                   '[{"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -56}]}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert view.wifi_radio_line(now=1000.0 + view.STALE_ROUTER_S + 60).endswith("STALE")


def test_view_line_does_not_invent_a_missing_channel(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000.0, "ok": true, "radios": '
                   '[{"band": "5", "channel": null, "freq_ghz": null, "noise_dbm": -56}]}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert "5 GHz channel UNKNOWN" in view.wifi_radio_line(now=1060.0)


care_spec = importlib.util.spec_from_file_location("network_care_wifi_under_test", REPO / "checks" / "network-care.py")
care = importlib.util.module_from_spec(care_spec)
care_spec.loader.exec_module(care)

NOW = 1_800_000_000.0


def wifi_row(ts, radios=None, ok=True):
    stats = {"tx_ok": 5000, "tx_fail": 50, "temp_c": 60, "tx_per": 3.3}
    radios = [{"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -55, "stats": stats},
              {"band": "5", "channel": 40, "freq_ghz": 5.2, "noise_dbm": -55, "stats": stats}] \
        if radios is None else radios
    return {"ts": ts, "ok": ok, "radios": radios}


def test_detector_unknown_when_one_bands_counters_are_missing():
    radios = [{"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -55,
               "stats": {"tx_ok": 5000, "tx_fail": 50}},
              {"band": "5", "channel": 40, "freq_ghz": 5.2, "noise_dbm": -55}]
    code, message = care.wifi_state([wifi_row(NOW - 60, radios=radios)], NOW)
    assert code == 3, message
    assert "counters missing on 5 GHz" in message


def test_detector_unknown_when_the_counters_are_missing():
    no_stats = [{"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -55}]
    code, message = care.wifi_state([wifi_row(NOW - 60, radios=no_stats)], NOW)
    assert code == 3, message
    assert "counters missing" in message


def test_detector_unknown_when_the_drivers_frame_loss_is_missing():
    stats = {"tx_ok": 5000, "tx_fail": 50, "temp_c": 60}  # the driver printed no PER after its counter
    radios = [{"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": -55, "stats": stats},
              {"band": "5", "channel": 40, "freq_ghz": 5.2, "noise_dbm": -55,
               "stats": dict(stats, tx_per=0.1)}]
    code, message = care.wifi_state([wifi_row(NOW - 60, radios=radios)], NOW)
    assert code == 3, message
    assert "frame-loss rate missing on 2.4 GHz" in message and "no PER" in message


def test_detector_is_green_on_a_fresh_reading():
    code, message = care.wifi_state([wifi_row(NOW - 60)], NOW)
    assert code == 0, message
    assert "2.4 GHz ch 13" in message and "5 GHz ch 40" in message


def test_detector_unknown_without_a_radio_reading():
    assert care.wifi_state([], NOW)[0] == 3
    assert care.wifi_state([wifi_row(NOW - 60, radios=[])], NOW)[0] == 3
    assert care.wifi_state([wifi_row(NOW - 60, ok=False)], NOW)[0] == 3


def test_detector_unknown_when_the_reading_is_stale():
    code, message = care.wifi_state([wifi_row(NOW - care.WIFI_MAX_AGE_S - 60)], NOW)
    assert code == 3, message


def test_detector_unknown_when_channel_or_noise_missing():
    partial = [{"band": "5", "channel": 40, "freq_ghz": 5.2, "noise_dbm": None}]
    assert care.wifi_state([wifi_row(NOW - 60, radios=partial)], NOW)[0] == 3


def test_detector_red_on_an_impossible_noise_value():
    bad = [{"band": "2.4", "channel": 13, "freq_ghz": 2.472, "noise_dbm": 12}]
    code, message = care.wifi_state([wifi_row(NOW - 60, radios=bad)], NOW)
    assert code == 2, message


def test_network_wifi_duty_is_registered():
    items = json.loads((REPO / "duties.json").read_text())["duties"]
    (duty,) = [d for d in items if d.get("name") == "network-wifi"]
    assert duty["resolver"] == "network" and duty["max_age"] == 900
    assert duty["unknown_codes"] == [3] and duty["red_codes"] == [2]
    assert "wifi" in duty["command"]
