import json
from mishe_tauftauf import ci_watch
from mishe_tauftauf.feed import Feed


def test_wall_ci_routes_failure_without_ledger_or_delivery(tmp_path, monkeypatch):
    (tmp_path / "coordination-mode.json").write_text(json.dumps({"mode": "wall"}))
    monkeypatch.setattr(ci_watch, "read", lambda h: {"state": "fail", "sha": "a"*40, "run": "1", "url": "https://example.invalid/run", "detail": "failed"})
    from mishe_tauftauf import delivery
    monkeypatch.setattr(delivery, "check_all", lambda h: (_ for _ in ()).throw(AssertionError("legacy delivery invoked")))
    ci_watch.tick(tmp_path)
    entries = Feed(tmp_path).entries()
    assert any(e.body.startswith("[dm] to=genome") for e in entries)
    assert not any(e.body.startswith("[task]") for e in entries)
