"""Clients the router lists with unchanged rates and signal are flagged as an observation.

A client whose numbers never move while others do is worth a look (the 2026-11-21 iMac entry sat at
rx 144 / tx 130 / -29 dBm for six reads). Nothing flags a client that is merely quiet on its own.
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
view = load(REPO / "tools" / "network_view.py", "network_view_frozen_under_test")


def read(ts, clients, ok=True):
    return {"ts": ts, "ok": ok, "clients": clients}


def client(name, rx, tx, dbm):
    return {"name": name, "rx": rx, "tx": tx, "dbm": dbm, "band": "2.4"}


def test_unchanged_client_is_flagged_when_others_move():
    rows = [read(i, [client("imac", 144.0, 130.0, -29), client("ipad", 80.0 + i, 60.0, -33)])
            for i in range(view.FROZEN_READS)]
    assert view.frozen_clients(rows) == ["imac"]


def test_nothing_flagged_when_every_client_is_steady():
    rows = [read(i, [client("imac", 144.0, 130.0, -29), client("ipad", 86.0, 172.0, -34)])
            for i in range(view.FROZEN_READS)]
    assert view.frozen_clients(rows) == []


def test_nothing_flagged_with_too_few_good_reads():
    rows = [read(i, [client("imac", 144.0, 130.0, -29), client("ipad", 80.0 + i, 60.0, -33)])
            for i in range(view.FROZEN_READS - 1)]
    assert view.frozen_clients(rows) == []


def test_failed_reads_do_not_count_toward_the_window():
    rows = [read(i, [client("imac", 144.0, 130.0, -29), client("ipad", 80.0 + i, 60.0, -33)])
            for i in range(view.FROZEN_READS - 1)]
    rows.append(read(99, None, ok=False))
    assert view.frozen_clients(rows) == []


def test_a_client_that_changed_once_is_not_frozen():
    rows = [read(i, [client("imac", 144.0 if i != 3 else 120.0, 130.0, -29),
                     client("ipad", 80.0 + i, 60.0, -33)])
            for i in range(view.FROZEN_READS)]
    assert view.frozen_clients(rows) == []


def test_recorded_line_names_the_frozen_client(monkeypatch):
    rows = [read(1000 + i, [client("iMac-Rozalia", 144.0, 130.0, -29), client("iPad", 80.0 + i, 60.0, -33)])
            for i in range(view.FROZEN_READS)]
    monkeypatch.setattr(view, "router_rows", lambda: rows)
    line = view.recorded_clients_line(now=1000 + view.FROZEN_READS)
    assert "unchanged over the last" in line
    assert "iMac-Rozalia" in line
