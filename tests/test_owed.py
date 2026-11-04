import pytest

from mishe_tauftauf import intentions, owed, signal_store as store


@pytest.fixture
def home(tmp_path):
    store.register(tmp_path, "ci", max_age=60, subscribers=["genome"])
    store.register(tmp_path, "budget", max_age=600, subscribers=[])
    return tmp_path


def test_nothing_owed_when_nothing_happened(home):
    assert owed.check(home, "genome", now=100.0).reasons == []


def test_inbox_touch_makes_role_owed_until_cursor_advances(home):
    owed.touch_inbox(home, "genome")
    snap = owed.check(home, "genome", now=100.0)
    assert snap.reasons == ["inbox"]
    owed.advance(home, "genome", snap)
    assert owed.check(home, "genome", now=101.0).reasons == []


def test_inbox_arriving_during_a_wake_stays_owed(home):
    owed.touch_inbox(home, "genome")
    snap = owed.check(home, "genome", now=100.0)
    owed.touch_inbox(home, "genome")           # lands mid-wake
    owed.advance(home, "genome", snap)
    assert owed.check(home, "genome", now=101.0).reasons == ["inbox"]


def test_signal_edge_owes_subscriber_only(home):
    store.observe(home, "ci", "RED", now=100.0, source="t")
    assert owed.check(home, "genome", now=100.0).reasons == ["signal:ci"]
    assert owed.check(home, "docs", now=100.0).reasons == []


def test_seen_refresh_does_not_owe(home):
    store.observe(home, "ci", "GREEN", now=100.0, source="t")
    owed.advance(home, "genome", owed.check(home, "genome", now=100.0))
    store.observe(home, "ci", "GREEN", now=130.0, source="t")
    assert owed.check(home, "genome", now=131.0).reasons == []


def test_expiry_owes_subscriber(home):
    store.observe(home, "ci", "GREEN", now=100.0, source="t")
    owed.advance(home, "genome", owed.check(home, "genome", now=100.0))
    store.expire(home, now=200.0)
    assert owed.check(home, "genome", now=200.0).reasons == ["signal:ci"]


def test_due_intention_owes_and_is_consumed_on_advance(home):
    intentions.add(home, "genome", "look", note="check ci", due=150.0)
    assert owed.check(home, "genome", now=100.0).reasons == []
    snap = owed.check(home, "genome", now=151.0)
    assert snap.reasons == ["intention:look"]
    owed.advance(home, "genome", snap)
    assert intentions.list_(home, "genome") == []


def test_future_intention_survives_advance(home):
    intentions.add(home, "genome", "later", note="n", due=500.0)
    intentions.add(home, "genome", "now", note="n", due=10.0)
    owed.advance(home, "genome", owed.check(home, "genome", now=100.0))
    assert [i["id"] for i in intentions.list_(home, "genome")] == ["later"]


def test_when_condition_on_signal_value(home):
    intentions.add(home, "genome", "red", note="n", when={"signal": "ci", "equals": "RED"})
    store.observe(home, "ci", "GREEN", now=100.0, source="t")
    assert "intention:red" not in owed.check(home, "genome", now=100.0).reasons
    store.observe(home, "ci", "RED", now=101.0, source="t")
    assert "intention:red" in owed.check(home, "genome", now=101.0).reasons


def test_spontaneous_intention_held_unless_budget_green(home):
    intentions.add(home, "genome", "idle", note="n", due=10.0, spontaneous=True)
    held = owed.check(home, "genome", now=100.0)           # budget signal absent = UNKNOWN
    assert held.reasons == [] and held.held == ["intention:idle"]
    store.observe(home, "budget", "RED", now=100.0, source="t")
    assert owed.check(home, "genome", now=100.0).reasons == []
    store.observe(home, "budget", "GREEN", now=101.0, source="t")
    assert owed.check(home, "genome", now=101.0).reasons == ["intention:idle"]


def test_external_trigger_is_not_gated_by_budget(home):
    owed.touch_inbox(home, "genome")
    assert owed.check(home, "genome", now=100.0).reasons == ["inbox"]


def test_corrupt_cursor_is_not_treated_as_nothing_owed(home):
    (home / "cursors").mkdir()
    (home / "cursors" / "genome.json").write_text("{oops")
    assert owed.check(home, "genome", now=1.0).reasons == ["cursor-unreadable"]
