"""The network window's VPN line reads sing-box presence and tun0 from the router's read-only pass.

A missing or unreadable process list is UNKNOWN, never "not running"; no command line is kept.
"""
import importlib.util
import sys
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
