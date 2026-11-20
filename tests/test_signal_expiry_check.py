"""Exercise the independent expiry check and its producer route."""

import importlib.util
from pathlib import Path
import time

import pytest

from mishe_tauftauf import duties, owed, producers
from mishe_tauftauf import signal_store as store

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("signal_expiry_check", REPO / "checks/signal-expiry.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
NAME = "signal-expiry"


def _observe(home, now):
    store.register(home, "probe", max_age=30.0, now=now)
    store.observe(home, "probe", "GREEN", now=now, source="fixture")


def test_check_distinguishes_a_late_edge_from_a_normal_retry(tmp_path):
    _observe(tmp_path, 1000.0)
    assert check.evaluate(tmp_path, 1330.0)[0] == 0
    code, message = check.evaluate(tmp_path, 1331.0)
    assert code == 2 and "probe" in message
    assert store.recorded(tmp_path, "probe")["state"] == store.OK
    assert store.expire(tmp_path, now=1331.0) == ["probe"]
    assert check.evaluate(tmp_path, 1331.0)[0] == 0


@pytest.mark.parametrize("fault", ["registry", "corrupt", "missing-reading", "missing-stamp"])
def test_unreadable_evidence_is_unknown(tmp_path, fault):
    _observe(tmp_path, 1000.0)
    signals = tmp_path / "signals"
    if fault == "registry":
        (signals / "registry.json").unlink()
    elif fault == "corrupt":
        (signals / "probe.json").write_bytes(b"{")
    elif fault == "missing-reading":
        (signals / "probe.json").unlink()
    else:
        (signals / "probe.seen").unlink()
    assert check.evaluate(tmp_path, 1331.0)[0] == 3
    if fault == "corrupt":
        assert (signals / "probe.json").read_bytes() == b"{"


def test_producer_routes_a_missing_expiry_edge_and_ages_when_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(producers.wake_memory, "running_unit_name", lambda: None)
    now = time.time()
    _observe(tmp_path, now - 600)
    duty = next(d for d in duties.declared(REPO, tmp_path) if d["name"] == NAME)
    assert duty["every"] == 300
    store.register(tmp_path, NAME, max_age=duty["signal"]["max_age"],
                   subscribers=duty["signal"]["subscribers"], now=now)

    def run():
        return producers.run(tmp_path, NAME, duty["command"], now=now, timeout=duty["timeout"],
                             red_codes=duty["red_codes"], unknown_codes=duty["unknown_codes"])

    assert run() == 0
    signal = store.read(tmp_path, NAME, now=now)
    assert signal.value == "RED" and "probe" in signal.reason
    assert owed.check(tmp_path, "rust-plan", now=now).reasons == [f"signal:{NAME}"]
    assert owed.check(tmp_path, "genome", now=now).reasons == []
    assert store.expire(tmp_path, now=now) == ["probe"]
    assert run() == 0
    assert store.read(tmp_path, NAME, now=now).value == "GREEN"
    assert store.read(tmp_path, NAME, now=now + duty["signal"]["max_age"] + 1).state == store.UNKNOWN

    (tmp_path / "signals" / "probe.json").write_bytes(b"{")
    assert run() == 2
    assert store.read(tmp_path, NAME, now=now).state == store.UNKNOWN
