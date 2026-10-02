from datetime import datetime, timezone, timedelta
import json
from mishe_tauftauf import seed
from mishe_tauftauf.feed import Feed


def test_expired_trial_still_delivers_addressed_escalation(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux
    (tmp_path/"coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}))
    alert = Feed(tmp_path).append("silence-watch", "[dm] to=health\nTrial window ended: decide whether to re-arm or report.")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    assert wall.tick(tmp_path, "session", "health", 300).startswith("wake ")
    wakes = [e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake health ")]
    assert len(wakes) == 1 and "addressed message awaits" in wakes[0].body
    assert str(alert.sequence) in sent[-1]
    # A settled escalation is answered once; the next tick creates no new wake.
    Feed(tmp_path).append("seed", f"seed yield health wake={wakes[0].sequence}")
    assert "ended" in wall.tick(tmp_path, "session", "health", 300)
    assert len([e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake health ")]) == 1


def test_expired_trial_without_a_message_creates_no_wake(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux
    (tmp_path/"coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}))
    Feed(tmp_path).append("genome", "Ordinary shared note, not addressed to health.")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda target, text: None)
    assert "ended" in wall.tick(tmp_path, "session", "health", 300)
    assert not [e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake ")]
