"""The network window's WI-FI line shows how old its listing is.

The listing is a live read cached for ROUTER_EVERY_S; a line without an age would look as fresh as the
RECORDED line when it may be a minute older. A cached listing must say its age; no listing must say UNKNOWN.
"""
import importlib.util
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
view = load(REPO / "tools" / "network_view.py", "network_view_wifi_age_under_test")


def test_cached_wifi_listing_shows_its_age(monkeypatch):
    monkeypatch.setitem(view._wifi, "at", time.time() - 42)
    monkeypatch.setitem(view._wifi, "text", "8 clients (2.4 GHz 6; 5 GHz 2), weakest -37 dBm on 5 GHz")
    monkeypatch.setitem(view._wifi, "weak", False)
    line = view.wifi_line()
    assert line.startswith("8 clients")
    assert "read 42 s ago" in line or "read 43 s ago" in line


def test_unreadable_wifi_listing_is_unknown(monkeypatch):
    monkeypatch.setitem(view._wifi, "at", time.time())
    monkeypatch.setitem(view._wifi, "text", None)
    assert view.wifi_line().startswith("UNKNOWN")
