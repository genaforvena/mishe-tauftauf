"""The network window's VPN line reads sing-box presence and tun0 from the router's read-only pass.

A missing or unreadable process list is UNKNOWN, never "not running"; no command line is kept.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
boundary = load(REPO / "tools" / "network_boundary.py", "network_boundary_vpn_under_test")
view = load(REPO / "tools" / "network_view.py", "network_view_vpn_under_test")

SING_BOX = "11631 root 1394m S /usr/bin/sing-box run -c /etc/sing-box/config.json"
TUN_UP = "tun0  UNKNOWN  <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP>"


def test_running_and_tun_up():
    v = boundary.decode_router_vpn(["PS_OK", SING_BOX, TUN_UP])
    assert v == {"ps_ok": True, "sing_box": True, "tun0": "up"}


def test_process_list_readable_but_no_sing_box_is_a_real_negative():
    v = boundary.decode_router_vpn(["PS_OK"])
    assert v == {"ps_ok": True, "sing_box": False, "tun0": "absent"}


def test_process_list_not_read_is_unknown_not_not_running():
    v = boundary.decode_router_vpn([])
    assert v["ps_ok"] is False and v["sing_box"] is None and v["tun0"] is None


def test_tun_down_is_reported_down():
    v = boundary.decode_router_vpn(["PS_OK", SING_BOX, "tun0  DOWN  <POINTOPOINT,NOARP>"])
    assert v["tun0"] == "down"


def test_command_line_is_never_kept():
    v = boundary.decode_router_vpn(["PS_OK", SING_BOX, TUN_UP])
    assert "config" not in repr(v) and "sing-box" not in repr(v)


def test_router_output_carries_vpn_section():
    stdout = "\n".join([
        "UPTIME 958323.10",
        "LOAD", "0.27 0.29 0.30 1/200 999", "MemAvailable: 200000 kB",
        "LINKS",
        "VPN", "PS_OK", SING_BOX, TUN_UP,
    ])
    body = boundary.decode_router_output(stdout, {}, lambda mac: "x")
    assert body["vpn"] == {"ps_ok": True, "sing_box": True, "tun0": "up"}


def test_vpn_line_says_running_and_up(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000.0, "ok": true, "vpn": {"ps_ok": true, "sing_box": true, "tun0": "up"}}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    line = view.vpn_line(now=1060.0)
    assert line.startswith("sing-box running, tun0 up")
    assert "STALE" not in line


def test_vpn_line_unknown_when_no_reading(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000.0, "ok": true}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert view.vpn_line(now=1060.0).startswith("UNKNOWN")


def test_vpn_line_marks_old_reading_stale(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000.0, "ok": true, "vpn": {"ps_ok": true, "sing_box": true, "tun0": "up"}}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert view.vpn_line(now=1000.0 + view.STALE_ROUTER_S + 60).endswith("STALE")


def test_vpn_line_shows_unreadable_process_list(tmp_path, monkeypatch):
    log = tmp_path / "router.jsonl"
    log.write_text('{"ts": 1000.0, "ok": true, "vpn": {"ps_ok": false, "sing_box": null, "tun0": "up"}}\n')
    monkeypatch.setattr(view, "ROUTER_LOG", log)
    assert "sing-box UNKNOWN" in view.vpn_line(now=1060.0)


care_spec = importlib.util.spec_from_file_location("network_care_vpn_under_test", REPO / "checks" / "network-care.py")
care = importlib.util.module_from_spec(care_spec)
care_spec.loader.exec_module(care)

NOW = 1_800_000_000.0


def vpn_row(ts, sing_box=True, tun0="up", ps_ok=True, ok=True):
    return {"ts": ts, "ok": ok, "vpn": {"ps_ok": ps_ok, "sing_box": sing_box, "tun0": tun0}}


class VpnDetectorTests(unittest.TestCase):
    def test_no_reading_is_unknown(self):
        self.assertEqual(care.vpn_state([], NOW)[0], 3)

    def test_healthy_reading_is_green(self):
        code, message = care.vpn_state([vpn_row(NOW - 60)], NOW)
        self.assertEqual(code, 0, message)
        self.assertIn("sing-box running", message)

    def test_sing_box_not_running_is_red(self):
        code, message = care.vpn_state([vpn_row(NOW - 60, sing_box=False, tun0="absent")], NOW)
        self.assertEqual(code, 2, message)
        self.assertIn("NOT running", message)

    def test_tun_down_is_red(self):
        self.assertEqual(care.vpn_state([vpn_row(NOW - 60, tun0="down")], NOW)[0], 2)

    def test_tun_absent_is_red(self):
        self.assertEqual(care.vpn_state([vpn_row(NOW - 60, tun0="absent")], NOW)[0], 2)

    def test_unreadable_process_list_is_unknown_not_red(self):
        code, _ = care.vpn_state([vpn_row(NOW - 60, ps_ok=False, sing_box=None)], NOW)
        self.assertEqual(code, 3)

    def test_unknown_link_is_unknown(self):
        self.assertEqual(care.vpn_state([vpn_row(NOW - 60, tun0=None)], NOW)[0], 3)

    def test_stale_newest_reading_is_unknown_not_green(self):
        self.assertEqual(care.vpn_state([vpn_row(NOW - 3600)], NOW)[0], 3)

    def test_failed_and_malformed_rows_are_ignored(self):
        rows = [vpn_row(NOW - 60, ok=False), {"ts": "x"}, vpn_row(NOW - 30)]
        self.assertEqual(care.vpn_state(rows, NOW)[0], 0)

    def test_old_green_does_not_hide_new_negative(self):
        rows = [vpn_row(NOW - 600), vpn_row(NOW - 60, sing_box=False, tun0="absent")]
        self.assertEqual(care.vpn_state(rows, NOW)[0], 2)

    def test_network_vpn_duty_runs_the_vpn_check_with_the_standard_codes(self):
        duties = json.loads((REPO / "duties.json").read_text(encoding="utf-8"))
        items = duties if isinstance(duties, list) else duties.get("duties", [])
        (duty,) = [d for d in items if d.get("name") == "network-vpn"]
        self.assertEqual(duty["command"][1:], ["{repo}/checks/network-care.py", "vpn", "--home", "{home}", "--repo", "{repo}"])
        self.assertEqual(duty["red_codes"], [2])
        self.assertEqual(duty["unknown_codes"], [3])
        self.assertEqual(duty["resolver"], "network")
