"""The network window's EXIT NODE line reads the egress duty's file and is never a calm GREEN by accident.

A missing, unreadable or stale file is UNKNOWN; a RED or unknown client is named.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "tools"))
view = load(REPO / "tools" / "network_view.py", "network_view_exit_under_test")


def write(tmp_path, results, age_s=0.0):
    path = tmp_path / "egress.json"
    path.write_text(json.dumps({"results": results}), encoding="utf-8")
    t = 1_000_000.0
    os.utime(path, (t, t))
    return path, t + age_s


def test_missing_file_is_unknown(tmp_path):
    line = view.exit_line(now=1.0, path=tmp_path / "absent.json")
    assert line.startswith("UNKNOWN")


def test_all_green_fresh_file(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "GREEN"}, {"client": "note3", "state": "GREEN"}], age_s=60)
    line = view.exit_line(now=now, path=path)
    assert line.startswith("all GREEN: mesh-home GREEN, note3 GREEN")
    assert "read 1 min ago" in line


def test_red_client_is_named(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "GREEN"}, {"client": "note3", "state": "RED"}])
    line = view.exit_line(now=now, path=path)
    assert line.startswith("1 of 2 not GREEN")
    assert "note3 RED" in line


def test_odd_state_is_unknown_not_green(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "BLUE"}])
    line = view.exit_line(now=now, path=path)
    assert "mesh-home UNKNOWN" in line
    assert "all GREEN" not in line


def test_stale_file_is_unknown(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "GREEN"}], age_s=0)
    line = view.exit_line(now=now + view.EGRESS_STALE_S + 60, path=path)
    assert line.startswith("UNKNOWN (last exit-node reading")


def test_corrupt_file_is_unknown(tmp_path):
    path = tmp_path / "egress.json"
    path.write_text("{not json", encoding="utf-8")
    assert view.exit_line(now=1.0, path=path).startswith("UNKNOWN (exit-node file could not be read")


def test_non_green_client_names_its_cause(tmp_path):
    path, now = write(tmp_path, [
        {"client": "mesh-home", "state": "GREEN", "reasons": []},
        {"client": "imac-rozalia", "state": "UNKNOWN",
         "reasons": ["the client could not be reached: ssh: connect to host 192.168.8.214 port 22: No route to host"]},
    ])
    line = view.exit_line(now=now, path=path, presence={})
    head, detail = line.split("\n")
    assert head.startswith("1 of 2 not GREEN")
    assert "mesh-home GREEN" in head and "imac-rozalia UNKNOWN" in head
    assert "imac-rozalia UNKNOWN: the client could not be reached" in detail
    assert "No route to host" not in detail or len(detail) <= 17 + 24 + 2 + view.REASON_CHARS


def test_a_client_still_on_wifi_is_explained_that_way(tmp_path):
    path, now = write(tmp_path, [
        {"client": "mesh-home", "state": "GREEN"},
        {"client": "imac-rozalia", "state": "UNKNOWN",
         "reasons": ["the client could not be reached: ssh: connect to host 192.168.8.214 port 22: No route to host"]},
    ])
    detail = view.exit_line(now=now, path=path, presence={"imacrozalia": -29}).split("\n")[1]
    assert "imac-rozalia UNKNOWN: still on Wi-Fi at -29 dBm" in detail
    assert "asleep" in detail
    assert "No route to host" not in detail
    assert len(detail) <= 17 + 24 + 2 + view.REASON_CHARS


def test_a_client_not_on_wifi_keeps_its_cause(tmp_path):
    path, now = write(tmp_path, [{"client": "note3", "state": "RED",
                                  "reasons": ["tailscale reports the exit node unreachable"]}])
    detail = view.exit_line(now=now, path=path, presence={"imacrozalia": -29}).split("\n")[1]
    assert "tailscale reports the exit node unreachable" in detail
    assert "still on Wi-Fi" not in detail


def test_wifi_presence_matches_the_router_name_and_ignores_a_stale_read(tmp_path):
    log = tmp_path / "router.jsonl"
    log.write_text(json.dumps({"ts": 1000.0, "ok": True,
                               "clients": [{"name": "iMac-Rozalia", "dbm": -29, "band": "2.4"}]}) + "\n",
                   encoding="utf-8")
    assert view.wifi_presence(now=1060.0, path=log) == {"imacrozalia": -29}
    assert view.wifi_presence(now=1000.0 + view.STALE_ROUTER_S + 60, path=log) == {}
    assert view.wifi_presence(now=1000.0, path=tmp_path / "absent.jsonl") == {}


def test_cause_line_stays_one_window_width(tmp_path):
    path, now = write(tmp_path, [{"client": "note3", "state": "RED",
                                  "reasons": ["x" * 400]}])
    detail = view.exit_line(now=now, path=path).split("\n")[1]
    assert detail.endswith("…")
    assert len(detail) <= 17 + 24 + 2 + view.REASON_CHARS


def test_green_client_causes_are_not_shown(tmp_path):
    path, now = write(tmp_path, [{"client": "mesh-home", "state": "GREEN",
                                  "reasons": ["a stale note about something else"]}])
    assert "\n" not in view.exit_line(now=now, path=path)


def test_a_bad_client_without_reasons_keeps_the_head_only(tmp_path):
    path, now = write(tmp_path, [{"client": "note3", "state": "RED", "reasons": []}])
    assert "\n" not in view.exit_line(now=now, path=path)


def test_at_most_three_clients_are_explained(tmp_path):
    results = [{"client": f"c{i}", "state": "RED", "reasons": [f"cause {i}"]} for i in range(5)]
    path, now = write(tmp_path, results)
    lines = view.exit_line(now=now, path=path).split("\n")
    assert len(lines) == 1 + view.REASON_CLIENTS
