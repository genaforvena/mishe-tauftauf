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
    assert start["trigger"] == "intention:again" and start["decision"]["choice"] is None


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


def test_row_after_torn_fragment_starts_on_its_own_line(tmp_path):
    # A crash mid-write leaves a fragment without a newline; the next append must not
    # concatenate onto it, or both the fragment and the new row are lost.
    path = wake_gate_log.log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"kind": "sta')
    snap = _snap(["inbox"])
    wake_gate_log.record_start(tmp_path, "system1", "k1", snap=snap, mail=[], now=1.0)
    rows = wake_gate_log._rows(tmp_path)
    assert [r["kind"] for r in rows] == ["start"]
    assert rows[0]["key"] == "k1"


def test_torn_fragment_is_skipped_and_later_rows_still_pair(tmp_path):
    path = wake_gate_log.log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"kind": "start", "ro')
    snap = _snap(["inbox"])
    wake_gate_log.record_start(tmp_path, "system1", "k1", snap=snap, mail=[], now=1.0)
    _settle(tmp_path, "k1", "verified")
    wake_gate_log.record_end(tmp_path, "system1", "k1", state="yielded", detail="")
    rows = wake_gate_log._rows(tmp_path)
    assert [r["kind"] for r in rows] == ["start", "end"]
    assert rows[1]["result"] == "verified"
    wake_gate_log.record_start(tmp_path, "system1", "k2", snap=snap, mail=[], now=2.0)
    assert wake_gate_log._rows(tmp_path)[-1]["key"] == "k2"


def test_engine_raise_still_writes_the_end_row_and_propagates(tmp_path, monkeypatch):
    # The shadow log pairs every start with an end. An engine that raises something other than
    # EngineNotStarted/EngineTimeout must not leave the start row without its end row, and the
    # wake must still see the exception (the log never changes the outcome).
    import pytest
    from mishe_tauftauf import wake

    def boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(wake, "_execute_engine", boom)
    with pytest.raises(RuntimeError, match="engine exploded"):
        wake._execute(tmp_path, "system1", "k-raise", engine=None, now=1000.0, snap=_snap(["inbox"]),
                      mail=[], reconcile_of=None, repo=None, timeout=1, on_start=None)
    rows = _rows(tmp_path)
    assert [r["kind"] for r in rows] == ["start", "end"]
    assert rows[1]["key"] == "k-raise"
    assert rows[1]["state"] == "unknown"
    assert "RuntimeError" in rows[1]["detail"]


def test_unprintable_engine_exception_is_preserved_and_logged(tmp_path, monkeypatch):
    import pytest
    from mishe_tauftauf import wake

    class UnprintableError(Exception):
        def __str__(self):
            raise ValueError("cannot format")

    original = UnprintableError()

    def boom(*args, **kwargs):
        raise original

    monkeypatch.setattr(wake, "_execute_engine", boom)
    with pytest.raises(UnprintableError) as caught:
        wake._execute(tmp_path, "system1", "k-unprintable", engine=None, now=1000.0,
                      snap=_snap(["inbox"]), mail=[], reconcile_of=None, repo=None, timeout=1)
    assert caught.value is original
    rows = _rows(tmp_path)
    assert [row["kind"] for row in rows] == ["start", "end"]
    assert rows[1]["state"] == "unknown"
    assert "UnprintableError" in rows[1]["detail"]


def test_receipt_then_raise_is_not_the_same_dossier_verdict(tmp_path):
    # Reviewed counterexample (field-review 909f3b90): the engine writes a verified receipt and
    # then raises. The end row is state unknown with result verified; it must not count as a
    # verified wake for the next start's same_dossier verdict.
    snap = _snap(["inbox"])
    wake_gate_log.record_start(tmp_path, "system1", "k1", snap=snap, mail=[], now=1.0)
    _settle(tmp_path, "k1", "verified")
    wake_gate_log.record_end(tmp_path, "system1", "k1", state="unknown", detail="RuntimeError")
    wake_gate_log.record_start(tmp_path, "system1", "k2", snap=snap, mail=[], now=2.0)
    start = [r for r in _rows(tmp_path) if r["kind"] == "start"][-1]
    assert start["key"] == "k2" and start["same_dossier_as_last"] is False


def test_engine_receipt_then_raise_end_row_blocks_same_dossier(tmp_path, monkeypatch):
    # Caller-level version: the real _execute path with an engine that settles verified and raises.
    import pytest
    from mishe_tauftauf import wake

    snap = _snap(["inbox"])

    def settle_then_boom(home, role, key, **kwargs):
        _settle(home, key, "verified")
        raise RuntimeError("engine exploded after receipt")

    monkeypatch.setattr(wake, "_execute_engine", settle_then_boom)
    with pytest.raises(RuntimeError):
        wake._execute(tmp_path, "system1", "k-a", engine=None, now=1000.0, snap=snap,
                      mail=[], reconcile_of=None, repo=None, timeout=1, on_start=None)
    monkeypatch.undo()
    wake_gate_log.record_start(tmp_path, "system1", "k-b", snap=snap, mail=[], now=2000.0)
    end = [r for r in _rows(tmp_path) if r["kind"] == "end"][0]
    assert end["state"] == "unknown" and end["result"] == "verified"
    start = [r for r in _rows(tmp_path) if r["kind"] == "start"][-1]
    assert start["key"] == "k-b" and start["same_dossier_as_last"] is False


def test_start_row_carries_the_system_one_shadow_answer(tmp_path, monkeypatch):
    seen = {}

    def fake_decide(provider, question, instructions, state, criteria, *, threshold, post=None):
        from mishe_tauftauf import decision_provider as dp
        seen["state"] = state
        seen["criteria"] = sorted(criteria)
        return dp.Decision(question, "owed-work", {"owed-work": 0.9, "repeat": 0.1}, 0.8, "decided")

    class FakeProvider:
        pass

    from mishe_tauftauf import budget_producer, decision_provider as dp
    monkeypatch.setattr(dp, "load_providers", lambda path: {"routerai": FakeProvider()})
    monkeypatch.setattr(dp, "select", lambda providers, name: providers[name])
    monkeypatch.setattr(dp, "decide", fake_decide)
    snap = _snap(["signal:initiatives-system1"], {"initiatives-system1": 2})
    wake_gate_log.record_start(tmp_path, "system1", "k2", snap=snap, mail=[(9, "body")], now=5.0)
    row = _rows(tmp_path)[-1]
    assert row["decision"]["choice"] == "owed-work"
    assert row["decision"]["probabilities"] == {"owed-work": 0.9, "repeat": 0.1}
    assert sorted(row["decision"]) == ["choice", "confidence", "escalated", "probabilities", "reason"]
    assert row["decision"]["escalated"] is False
    assert seen["criteria"] == ["owed-work", "repeat"]
    assert "mail items: 1" in seen["state"] and "body" not in seen["state"]
    assert "same" not in seen["state"]  # the model never sees the System 0 verdict


def test_below_threshold_answer_is_logged_as_escalated_argmax(tmp_path, monkeypatch):
    from mishe_tauftauf import decision_provider as dp

    def low_confidence(provider, question, instructions, state, criteria, *, threshold, post=None):
        return dp.Decision(question, None, {"owed-work": 0.49, "repeat": 0.47}, 0.24,
                           "confidence 0.24 below threshold 0.70; escalate to a mind")

    monkeypatch.setattr(dp, "load_providers", lambda path: {"routerai": object()})
    monkeypatch.setattr(dp, "select", lambda providers, name: providers[name])
    monkeypatch.setattr(dp, "decide", low_confidence)
    wake_gate_log.record_start(tmp_path, "system1", "k5", snap=_snap(["intention:again"]), mail=[], now=8.0)
    decision = _rows(tmp_path)[-1]["decision"]
    assert decision["choice"] == "owed-work" and decision["escalated"] is True
    assert decision["confidence"] == 0.24 and "escalate" in decision["reason"]


def test_missing_provider_config_is_an_unanswered_start_not_a_failure(tmp_path):
    snap = _snap(["intention:again"])
    wake_gate_log.record_start(tmp_path, "system1", "k3", snap=snap, mail=[], now=6.0)
    row = _rows(tmp_path)[-1]
    assert row["kind"] == "start"
    assert row["decision"]["choice"] is None
    assert "not usable" in row["decision"]["reason"]


def test_failing_provider_still_writes_the_start_row(tmp_path, monkeypatch):
    from mishe_tauftauf import decision_provider as dp

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(dp, "load_providers", boom)
    wake_gate_log.record_start(tmp_path, "system1", "k4", snap=_snap(["inbox"]), mail=[], now=7.0)
    row = _rows(tmp_path)[-1]
    assert row["key"] == "k4" and row["decision"]["choice"] is None
