"""The shadow wake-gate log: one start row and one end row per wake, never a decision."""
from __future__ import annotations

import json

from mishe_tauftauf import wake_gate_log, yielding
from mishe_tauftauf.owed import Snapshot


def _snap(reasons, revs=None):
    return Snapshot(reasons=list(reasons), signal_revs=dict(revs or {}))


def _rows(home):
    return [json.loads(line) for line in wake_gate_log.log_path(home).read_text().splitlines()]


def _settle(home, key, result):
    path = yielding.receipt_path(home, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": key, "result": result}))


def test_hash_ignores_message_bodies_and_order(tmp_path):
    a = wake_gate_log.dossier_inputs(_snap(["inbox", "signal:x"], {"x": 3}), [(7, {"body": "one"}), (5, "two")])
    b = wake_gate_log.dossier_inputs(_snap(["signal:x", "inbox"], {"x": 3}), [(5, "other"), (7, {"body": "two"})])
    assert wake_gate_log.dossier_hash(a) == wake_gate_log.dossier_hash(b)
    c = wake_gate_log.dossier_inputs(_snap(["inbox", "signal:x"], {"x": 4}), [(7, "one"), (5, "two")])
    assert wake_gate_log.dossier_hash(a) != wake_gate_log.dossier_hash(c)


def test_same_dossier_after_verified_wake_is_the_system0_verdict(tmp_path):
    snap = _snap(["intention:again"], {"s": 1})
    wake_gate_log.record_start(tmp_path, "system1", "k1", snap=snap, mail=[], now=1.0)
    _settle(tmp_path, "k1", "verified")
    wake_gate_log.record_end(tmp_path, "system1", "k1", state="yielded", detail="engine exit 0")
    wake_gate_log.record_start(tmp_path, "system1", "k2", snap=snap, mail=[], now=2.0)
    start = [r for r in _rows(tmp_path) if r["kind"] == "start"][-1]
    assert start["key"] == "k2" and start["same_dossier_as_last"] is True
    assert start["trigger"] == "intention:again" and start["decision"] is None


def test_changed_last_wake_does_not_mark_same_dossier(tmp_path):
    snap = _snap(["inbox"])
    wake_gate_log.record_start(tmp_path, "system1", "k1", snap=snap, mail=[(1, "x")], now=1.0)
    _settle(tmp_path, "k1", "changed")
    wake_gate_log.record_end(tmp_path, "system1", "k1", state="yielded", detail="")
    wake_gate_log.record_start(tmp_path, "system1", "k2", snap=snap, mail=[(1, "x")], now=2.0)
    assert [r for r in _rows(tmp_path) if r["kind"] == "start"][-1]["same_dossier_as_last"] is False


def test_end_row_without_receipt_records_unknown_result(tmp_path):
    wake_gate_log.record_end(tmp_path, "system1", "k9", state="unknown", detail="engine timed out")
    row = _rows(tmp_path)[-1]
    assert row == {"kind": "end", "role": "system1", "key": "k9", "ts": row["ts"],
                   "state": "unknown", "result": None, "detail": "engine timed out"}


def test_logging_failure_never_raises(tmp_path):
    blocker = tmp_path / "decision-log"
    blocker.write_text("not a directory")          # makes the append fail
    wake_gate_log.record_start(tmp_path, "system1", "k1", snap=None, mail=[], now=1.0)
    wake_gate_log.record_end(tmp_path, "system1", "k1", state="failed", detail="x")
