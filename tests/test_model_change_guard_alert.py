from pathlib import Path

from mishe_tauftauf import model_change


class _Recorder:
    sent: list = []

    def __init__(self, home):
        self.home = home

    def append_runtime_once(self, source, message):
        _Recorder.sent.append((source, message))


def _home(tmp_path: Path) -> Path:
    (tmp_path / 'checks').mkdir()
    return tmp_path


def test_repeated_guard_failures_send_one_message(tmp_path, monkeypatch):
    monkeypatch.setattr(model_change, 'Feed', _Recorder)
    monkeypatch.setattr(model_change, 'site_home', lambda home: Path(home))
    _Recorder.sent = []
    home = _home(tmp_path)
    # Thirty-second timer ticks for ten minutes: the unit fails every tick.
    results = [model_change.route_guard_failure(home, at=1000.0 + 30 * i) for i in range(20)]
    assert results[0] == 0
    assert all(r is None for r in results[1:])
    assert len(_Recorder.sent) == 1
    source, message = _Recorder.sent[0]
    assert source == 'model-change'
    assert 'Resolver=health' in message


def test_changed_state_is_not_muted(tmp_path, monkeypatch):
    # The edge gate keys on the signature; a changed signature must still send.
    monkeypatch.setattr(model_change, 'Feed', _Recorder)
    monkeypatch.setattr(model_change, 'site_home', lambda home: Path(home))
    _Recorder.sent = []
    home = _home(tmp_path)
    model_change.route_guard_failure(home, at=1000.0)
    alerts_path = home / 'checks' / 'model-change-alerts.json'
    from mishe_tauftauf import model_boundary as boundary
    alerts = boundary.load(alerts_path)
    alerts[model_change.GUARD_ALERT_KEY]['signature'] = 'guard-failed-earlier'
    boundary.save(alerts_path, alerts)
    assert model_change.route_guard_failure(home, at=1030.0) == 0
    assert len(_Recorder.sent) == 2


def test_unreadable_memory_re_alerts_once_and_does_not_mute(tmp_path, monkeypatch):
    monkeypatch.setattr(model_change, 'Feed', _Recorder)
    monkeypatch.setattr(model_change, 'site_home', lambda home: Path(home))
    _Recorder.sent = []
    home = _home(tmp_path)
    (home / 'checks' / 'model-change-alerts.json').write_text('{not json')
    assert model_change.route_guard_failure(home, at=1000.0) == 0
    assert len(_Recorder.sent) == 1
