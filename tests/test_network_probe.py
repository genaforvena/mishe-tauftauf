"""The network probe and window: a failed or missing check must never look like a calm network."""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = load(REPO / "checks" / "network_probe.py", "network_probe_under_test")
sys.path.insert(0, str(REPO / "tools"))
view = load(REPO / "tools" / "network_view.py", "network_view_under_test")


def test_parse_fetch_accepts_expected_status_only():
    ok = probe.parse_fetch("204 0.002 0.798 0.863", 0, 204)
    assert ok == {"ok": True, "connect_ms": 2.0, "tls_ms": 798.0, "total_ms": 863.0}


def test_parse_fetch_failure_is_not_ok():
    # curl exit 28 = timeout: the fake address that a router "answers" for must not count as reachable
    assert probe.parse_fetch("000 0.000 0.000 8.001", 28, 204) == {"ok": False, "curl_exit": 28}
    # wrong status from a captive or intercepting page is not a pass either
    assert probe.parse_fetch("302 0.01 0.02 0.03", 0, 204)["ok"] is False


def test_view_verdict_down_when_no_secure_site_answers(tmp_path):
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


def test_probe_exits_red_and_window_renders_in_a_scratch_home(tmp_path):
    # Runs the real probe once against a scratch home (bounded: a few pings and two small HTTPS fetches).
    r = subprocess.run([sys.executable, str(REPO / "checks" / "network_probe.py"), "--home", str(tmp_path)],
                       capture_output=True, text=True, timeout=90)
    assert r.returncode in (0, 2), r.stderr
    row = json.loads((tmp_path / "network" / "probe.jsonl").read_text().splitlines()[-1])
    assert set(row["internet"]) == {"gstatic", "cloudflare"}
    out = subprocess.run([sys.executable, str(REPO / "tools" / "network_view.py"), "--once"],
                         capture_output=True, text=True, timeout=60,
                         env={"MISHE_TAUFTAUF_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert "HOME INTERNET:" in out.stdout
