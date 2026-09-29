from mishe_tauftauf.feed import Feed
from mishe_tauftauf.seed import _external_event, _observation_text


def test_scoped_decision_wakes_owner_once(tmp_path):
    feed = Feed(tmp_path)
    decision = feed.append("operator/permissions", "[permission] id=sample decision=granted owner=senses task=sense-sample capability=read.sample unblocks=sense/sample")
    assert _external_event(tmp_path, "senses") == decision
    assert _external_event(tmp_path, "genome") is None
    feed.append("seed", f"seed wake senses observation=1 event={decision.sequence}")
    assert _external_event(tmp_path, "senses") is None


def test_scan_values_do_not_create_new_exploration_obligation():
    first = "GOAL: look\nVERIFIED sense.load: 1\nSCAN: first\nUNKNOWN sense.keyboard: unavailable\nSTATE: UNKNOWN\n"
    second = "GOAL: look\nVERIFIED sense.load: 2\nSCAN: second\nUNKNOWN sense.keyboard: unavailable\nSTATE: UNKNOWN\n"
    assert _observation_text("senses", first) == _observation_text("senses", second)
    assert _observation_text("genome", first) != _observation_text("genome", second)
