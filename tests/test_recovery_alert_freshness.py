import time
from mishe_tauftauf import recovery_io as recovery


def _deliver_text(tmp_path, monkeypatch, payload):
    root = tmp_path / "recovery"
    recovery.save(root / "outbox" / ("a" * 64 + "-health.json"), {"event": "a" * 64, "recipient": "health", "payload": {"host_capacity": payload}})
    texts = []
    def capture(argv, **kwargs):
        texts.append(argv[-1])
        return {"ok": None, "code": None, "stdout": "", "stderr": "controlled"}
    monkeypatch.setattr(recovery, "run_probe", capture)
    recovery.deliver(tmp_path)
    return texts[0]


def test_stale_capacity_alert_says_stale_and_dates_its_observation(tmp_path, monkeypatch):
    old = time.time() - 5400
    text = _deliver_text(tmp_path, monkeypatch, {"state": "LIMITED", "oom_delta": 0, "checked_at": old, "max_age": 20})
    assert "STALE observation" in text and "historical, not current" in text
    assert time.strftime("%H:%M:%SZ", time.gmtime(old)) in text


def test_fresh_capacity_alert_reports_its_age(tmp_path, monkeypatch):
    text = _deliver_text(tmp_path, monkeypatch, {"state": "CRITICAL", "oom_delta": 1, "checked_at": time.time() - 3, "max_age": 60})
    assert "observed " in text and "STALE" not in text and ("age 2s" in text or "age 3s" in text)


def test_missing_observation_time_is_unknown_not_current(tmp_path, monkeypatch):
    text = _deliver_text(tmp_path, monkeypatch, {"state": "LIMITED"})
    assert "treat as UNKNOWN, not current" in text and "disposition due unknown" in text


def test_future_dated_capacity_alert_is_unknown_not_fresh(tmp_path, monkeypatch):
    # Witness 165668: a checked_at ahead of the clock (skew or corrupt row) must not render as current
    # with a negative age; it takes the same UNKNOWN branch as a missing observation time.
    text = _deliver_text(tmp_path, monkeypatch, {"state": "LIMITED", "oom_delta": 0, "checked_at": time.time() + 500, "max_age": 20})
    assert "treat as UNKNOWN, not current" in text and "disposition due unknown" in text
    assert "observed " not in text and "age -" not in text
