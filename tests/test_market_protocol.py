import pytest

from mishe_tauftauf.feed import FeedEntry
from mishe_tauftauf.chat_protocol import ProtocolError
from mishe_tauftauf import task_state


def entry(sequence, body, source="genome"):
    return FeedEntry(sequence, "2026-10-29T18:00:00Z", source, body)


def test_market_malformed_reserved_record_is_not_silently_discarded():
    records = [entry(12, '[task-bid] repair\n{"identity":')]
    with pytest.raises(ProtocolError, match="entry 12"):
        task_state.task_bids(records, "repair")


def test_market_events_decode_legacy_and_typed_records_and_ignore_prose():
    records = [
        entry(1, "ordinary prose\nbid:repair:qual=qualified:available=0"),
        entry(2, "announce:repair:requires=qualified"),
        entry(3, "bid:repair:qual=qualified:available=42", "senses"),
        entry(4, '[task-bid] repair\n{"identity":"repair","bidder":"health","qual":"qualified","available_wake":43}'),
        entry(5, "award:repair:winner=senses:bids=2"),
    ]
    assert task_state.task_announcements(records)["repair"].sequence == 2
    assert [(b.bidder, b.available_wake) for b in task_state.task_bids(records, "repair")] == [("senses", 42), ("health", 43)]
    assert task_state.award_task(records, "repair").winner == "senses"
