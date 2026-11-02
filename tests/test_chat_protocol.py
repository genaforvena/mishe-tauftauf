import pytest

from mishe_tauftauf.feed import FeedEntry


def entry(body, source="seed"):
    return FeedEntry(23, "2026-10-07T10:00:00Z", source, body)


def test_decodes_explained_lifecycle_receipts_and_keeps_prose_opaque():
    from mishe_tauftauf.chat_protocol import decode_lifecycle
    wake = decode_lifecycle(entry("seed wake docs observation=12 event=13 task=doc-1\nHuman prose\nseed yield docs wake=23"))
    assert (wake.kind, wake.role, wake.observation, wake.event, wake.task) == ("wake", "docs", 12, 13, "doc-1")
    assert wake.prose == "Human prose\nseed yield docs wake=23"
    settled = decode_lifecycle(entry("seed yield docs wake=23 continue=1\nTurn settled (changed); human note\nextra"))
    assert (settled.kind, settled.role, settled.wake, settled.continue_work, settled.result) == ("yield", "docs", 23, True, "changed")
    clear = decode_lifecycle(entry("seed clear docs after=23\nIdle mind rotated."))
    assert (clear.kind, clear.role, clear.wake) == ("clear", "docs", 23)


@pytest.mark.parametrize("body", [
    "seed wake docs observation=0", "seed yield docs wake=0",
    "seed clear docs after=0", "seed yield docs wake=23garbage",
    "seed wake docs observation=12 unexpected=1", "seed yield docs wake=23 continue=0",
    " seed wake docs observation=12", "seed yield docs", "seed wake",
])
def test_malformed_reserved_receipts_are_visible_errors(body):
    from mishe_tauftauf.chat_protocol import ProtocolError, decode_lifecycle
    with pytest.raises(ProtocolError, match="23"):
        decode_lifecycle(entry(body))


def test_other_sources_and_ordinary_notes_do_not_become_control():
    from mishe_tauftauf.chat_protocol import decode_lifecycle
    assert decode_lifecycle(entry("seed wake docs observation=12", source="operator")) is None
    assert decode_lifecycle(entry("Human note\nseed wake docs observation=12")) is None
    assert decode_lifecycle(entry("seed observation docs")) is None


def test_consumers_agree_and_exact_wake_identity_matters():
    from mishe_tauftauf import seed, task_state, seed_board
    events = [entry("seed wake docs observation=12 task=doc-1")]
    events += [FeedEntry(24, events[0].timestamp, "seed", "seed yield docs wake=230\nTurn settled (verified); old turn")]
    assert task_state.active_wakes(events) == {"docs": 23}
    assert task_state.pending_tasks(events) == {("docs", 23): "doc-1"}
    assert seed._state(None, "docs", entries=events)[1] == 23
    events += [FeedEntry(25, events[0].timestamp, "seed", "seed yield docs wake=23\nTurn settled (changed); done")]
    assert task_state.active_wakes(events) == {}
    assert task_state.pending_tasks(events) == {}
    assert seed._state(None, "docs", entries=events)[1:3] == (None, 23)
    assert seed_board.work_receipts(events, "docs")[-1].result == "changed"


def test_malformed_receipts_cannot_leave_a_healthy_projection():
    from mishe_tauftauf import seed, task_state, seed_board
    from mishe_tauftauf.chat_protocol import ProtocolError
    events = [entry("seed wake docs observation=0")]
    for project in (task_state.active_wakes, task_state.pending_tasks, seed_board.work_receipts):
        with pytest.raises(ProtocolError):
            project(events, "docs") if project is seed_board.work_receipts else project(events)
    with pytest.raises(ProtocolError):
        seed._state(None, "docs", entries=events)


def test_malformed_lifecycle_control_reaches_dashboard_failure_detector(tmp_path, monkeypatch, capsys):
    from argparse import Namespace
    from mishe_tauftauf import cli, dashboard
    from mishe_tauftauf.feed import Feed
    from mishe_tauftauf.observations import RenderedPain
    Feed(tmp_path).append("seed", "seed wake docs observation=0")
    monkeypatch.setattr(cli, "compose_frame", lambda *a: RenderedPain("docs", "STATE: GREEN\n", True))
    monkeypatch.setattr(cli.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    cli.cmd_pain_watch(Namespace(home=tmp_path, slug="docs", timeout=1, interval=5))
    capsys.readouterr()
    frame, ok = dashboard.read(tmp_path, "docs")
    assert not ok and "STATE: RED" in frame and "malformed lifecycle" in frame
def test_grounding_ack_returns_hex_digest_and_ignores_nonhex():
    from mishe_tauftauf.chat_protocol import grounding_ack
    ok = grounding_ack(entry("hello grounded:d4c0698 world", source="senses"))
    assert ok == "d4c0698"
    assert grounding_ack(entry("grounded: nothex", source="senses")) is None
    assert grounding_ack(entry("no marker here", source="senses")) is None


def test_grounding_ack_requires_grounded_prefix():
    from mishe_tauftauf.chat_protocol import grounding_ack
    # Embedded hex without prefix must not match.
    assert grounding_ack(entry("seed wake docs observation=12 d4c0698", source="seed")) is None
    assert grounding_ack(entry("market announce:foo:requires=bar d4c0698", source="genome")) is None
    # With prefix it matches even on lifecycle/market-looking entries.
    assert grounding_ack(entry("seed wake docs observation=12\ngrounded:d4c0698b", source="seed")) == "d4c0698b"


def test_grounding_ack_accepts_7_to_40_hex():
    from mishe_tauftauf.chat_protocol import grounding_ack
    assert grounding_ack(entry("grounded:abc1234", source="senses")) == "abc1234"
    assert grounding_ack(entry("grounded:" + "a" * 40, source="senses")) == "a" * 40
    # 6 chars too short — not a grounded ack
    assert grounding_ack(entry("grounded:abc123", source="senses")) is None
    # 41 hex chars: not bounded by \b at 40, so no match (prevents accepting a
    # longer hex run as a 40-char prefix). Upper-case also not matched.
    assert grounding_ack(entry("grounded:" + "a" * 41, source="senses")) is None
    assert grounding_ack(entry("grounded:ABC1234", source="senses")) is None
