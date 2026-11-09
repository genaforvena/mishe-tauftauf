"""The network window's reading of probe rows: a failed or missing row must never look calm.

These are the view's tests (tools/network_view.py, tools/network_boundary.py).
The probe that writes the rows is a separate responsibility with its own
contract and its own tests (tests/test_mishe_network_probe.py).
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
