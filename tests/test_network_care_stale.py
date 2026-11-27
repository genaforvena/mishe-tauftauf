"""A stale network reading says whether its gap was a containment-gate skip (checks/network-care.py)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("network_care", ROOT / "checks" / "network-care.py")
care = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(care)


def test_gap_with_gate_skips_says_trip_and_stays_unknown(monkeypatch):
    monkeypatch.setattr(care, "gate_skips_between", lambda last, now: 28)
    msg = care.stale_message("router uplink", 1000.0, 1000.0 + 17 * 60)
    assert msg.startswith("network-care: newest router uplink reading is 17 min old")
    assert "28 network producer run(s) were skipped by their containment condition" in msg
    assert "not a dead probe" in msg


def test_gap_without_gate_skips_points_at_the_producer(monkeypatch):
    monkeypatch.setattr(care, "gate_skips_between", lambda last, now: 0)
    msg = care.stale_message("VPN", 1000.0, 1000.0 + 40 * 60)
    assert "check the producer itself" in msg


def test_unreadable_journal_keeps_plain_age_message(monkeypatch):
    monkeypatch.setattr(care, "gate_skips_between", lambda last, now: None)
    msg = care.stale_message("Wi-Fi radio", 1000.0, 1000.0 + 16 * 60)
    assert msg == "network-care: newest Wi-Fi radio reading is 16 min old"


def test_journal_bytes_that_are_not_utf8_are_counted_not_crashed(monkeypatch):
    # 2026-11-27: one journal line held a non-UTF-8 byte (0xc4); strict decoding crashed the router-load
    # gate and the duty went RED. The count must survive it.
    import subprocess

    raw = (b"mishe-producer-network-router-load: \xc4 odd bytes\n"
           b"mishe-producer-network-probe unmet condition check mishe-containment/tripped\n")

    def fake_run(argv, **kw):
        assert kw.get("errors") == "replace"
        return subprocess.CompletedProcess(argv, 0, stdout=raw.decode("utf-8", kw["errors"]), stderr="")

    monkeypatch.setattr(care.subprocess, "run", fake_run)
    assert care.gate_skips_between(1000.0, 2000.0) == 1
