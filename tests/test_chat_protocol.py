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
