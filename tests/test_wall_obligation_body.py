"""A wake's own body names its work; the delivery prompt must carry it.

Measured on the live site for pending wake 28366: `restore` carries 13,733 bytes
of constant doctrine but zero bytes of the role's wall, handoff or tape body, and
`wall.context`/`wall.pane` filter out `source == "seed"` entries. So the wake
receipt was the only record naming the owed work and it reached no mind-facing
surface — a woken mind could read only the wall, which names a settled turn.
"""

from mishe_tauftauf import seed
from mishe_tauftauf.feed import Feed


def _site(tmp_path):
    from mishe_tauftauf import wall, tmux
    from datetime import datetime, timezone, timedelta
    import json

    (tmp_path / "coordination-mode.json").write_text(json.dumps(
        {"mode": "wall", "until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}))
    return wall, tmux



def test_pending_wake_body_reaches_the_delivered_prompt(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux
    _site(tmp_path)
    later_task = "marrow-trestle"
    wake = Feed(tmp_path).append(
        "seed", f"seed wake health observation=7\nNew obligation: continue {later_task}.")
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))

    assert wall.deliver(tmp_path, "session", "health", wake.sequence, 7, "Sensor changed.") \
        == f"wake seed health {wake.sequence}"
    delivered = sent[-1]
    assert later_task in delivered
    assert f"OBLIGATION\nNew obligation: continue {later_task}." in delivered
    # The receipt line is emitted once, before the body.
    assert delivered.index(f"WAKE {wake.sequence}") < delivered.index("OBLIGATION")


def test_pending_wake_body_is_absent_before_the_fix_returns_a_bounded_fallback(
        tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    _site(tmp_path)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))

    # A wake sequence that has no tape entry: deliver must not raise, and must
    # surface the absence rather than a fabricated obligation.
    assert wall.deliver(tmp_path, "session", "health", 9999, 7, "Sensor changed.") \
        == "wake seed health 9999"
    assert "OBLIGATION\n(no pending wake body; reconcile against the tape)" in sent[-1]


def test_settled_wall_still_names_the_settled_turn_while_delivery_names_the_owed_work(
        tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux
    _site(tmp_path)
    settled_task = "helio-grindstone"
    later_task = "marrow-trestle"
    wake = Feed(tmp_path).append(
        "seed", f"seed wake health observation=7\nNew obligation: continue {later_task}.")
    (tmp_path / "walls").mkdir(exist_ok=True)
    (tmp_path / "walls" / "health.md").write_text(
        f"# Turn in progress — settled turn\n\nPENDING WORK: {settled_task}\n")
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    wall.deliver(tmp_path, "session", "health", wake.sequence, 7, "Sensor changed.")

    delivered = sent[-1]
    # The wall surface names the settled turn; the delivery names the owed work.
    assert settled_task in wall.pane(tmp_path, "health")
    assert later_task not in wall.pane(tmp_path, "health")
    assert later_task in delivered


def test_pending_body_strips_the_receipt_line_and_drops_a_bodyless_wake(tmp_path):
    from mishe_tauftauf import wall
    _site(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake health observation=7")
    assert wall._pending_body(tmp_path, wake.sequence) == "(pending wake carries no body)"


def test_pending_body_rejects_a_non_seed_entry_with_the_same_sequence(tmp_path):
    from mishe_tauftauf import wall
    _site(tmp_path)
    note = Feed(tmp_path).append("genome", "An ordinary shared note, not a wake.")
    assert wall._pending_body(tmp_path, note.sequence) == \
        "(no pending wake body; reconcile against the tape)"

def test_pending_body_rejects_a_seed_observation_at_the_same_sequence(tmp_path):
    from mishe_tauftauf import wall
    _site(tmp_path)
    # An observation is also source == "seed" but is not a wake; presenting its
    # body as owed work would name the wrong obligation (measured on the live
    # tape, where a stale observation can carry the same sequence as a wake).
    observation = Feed(tmp_path).append(
        "seed", "seed observation health\nFresh sensor snapshot saved.")
    assert wall._pending_body(tmp_path, observation.sequence) == \
        "(no pending wake body; reconcile against the tape)"
