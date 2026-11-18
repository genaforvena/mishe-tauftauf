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
    assert len(lines) == 2, lines
    assert lines[0].startswith("2.4 GHz ") and "driver's frame loss 3.3%" in lines[0], lines
    assert lines[1].startswith("5 GHz ") and "driver's frame loss 0.1%" in lines[1], lines
    assert lines[1].endswith("read 60 s ago"), lines
    # a read without the driver's PER keeps the attempts figure and invents no frame loss
    rows[-1]["radios"][0]["stats"].pop("tx_per")
    rows[-1]["radios"][1]["stats"].pop("tx_per")
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    line = view.wifi_quality_line(now=NOW)
    assert "frame loss" not in line and "since boot" in line, line



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
