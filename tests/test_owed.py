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


def test_role_uses_its_configured_budget_without_changing_aggregate(home):
    from mishe_tauftauf.state_files import write_json
    write_json(home / "engines/genome.json", dict(engine="claude", budget_signal="budget-claude"))
    store.register(home, "budget-claude", max_age=60, subscribers=[])
    store.observe(home, "budget", "RED", now=100, source="codex-forecast")
    store.observe(home, "budget-claude", "GREEN", now=100, source="claude-forecast")
    intentions.add(home, "genome", "idle", note="n", due=10, spontaneous=True)
    intentions.add(home, "docs", "idle", note="n", due=10, spontaneous=True)
    assert owed.check(home, "genome", now=100).reasons == ["intention:idle"]
    assert owed.check(home, "docs", now=100).held == ["intention:idle"]
    assert store.read(home, "budget", now=100).value == "RED"


@pytest.mark.parametrize("condition", ["missing", "stale", "red", "broken-config"])
def test_configured_budget_uncertainty_never_falls_back_to_green_aggregate(home, condition):
    from mishe_tauftauf.state_files import write_json
    path = home / "engines/genome.json"
    write_json(path, dict(engine="claude", budget_signal="budget-claude"))
    store.observe(home, "budget", "GREEN", now=100, source="aggregate")
    if condition != "missing":
        store.register(home, "budget-claude", max_age=60, subscribers=[])
        store.observe(home, "budget-claude", "RED" if condition == "red" else "GREEN", now=0 if condition == "stale" else 100, source="claude")
    if condition == "broken-config": path.write_text("{broken")
    intentions.add(home, "genome", "idle", note="n", due=10, spontaneous=True)
    assert owed.check(home, "genome", now=100).held == ["intention:idle"]


def test_corrupt_cursor_is_not_treated_as_nothing_owed(home):
    (home / "cursors").mkdir()
    (home / "cursors" / "genome.json").write_text("{oops")
    assert owed.check(home, "genome", now=1.0).reasons == ["cursor-unreadable"]


def _ids(home):
    return sorted(i["id"] for i in intentions.list_(home, "genome"))


def test_signal_fires_first_retires_the_paired_fallback(home):
    intentions.add(home, "genome", "again", note="n", due=500.0, pair="wake-1")
    intentions.add(home, "genome", "when-ci", note="n", when={"signal": "ci", "equals": "RED"}, pair="wake-1")
    store.observe(home, "ci", "RED", now=100.0, source="t")
    snap = owed.check(home, "genome", now=100.0)
    assert "intention:when-ci" in snap.reasons
    owed.advance(home, "genome", snap)
    assert _ids(home) == []


def test_fallback_fires_first_retires_the_paired_signal(home):
    intentions.add(home, "genome", "again", note="n", due=50.0, pair="wake-1")
    intentions.add(home, "genome", "when-ci", note="n", when={"signal": "ci", "equals": "RED"}, pair="wake-1")
    snap = owed.check(home, "genome", now=60.0)
    assert snap.reasons == ["intention:again"]
    owed.advance(home, "genome", snap)
    assert _ids(home) == []


def test_pairs_from_different_yields_do_not_interfere(home):
    intentions.add(home, "genome", "again", note="n", due=50.0, pair="wake-1")
    intentions.add(home, "genome", "when-ci", note="n", when={"signal": "ci", "equals": "RED"}, pair="wake-2")
    owed.advance(home, "genome", owed.check(home, "genome", now=60.0))
    assert _ids(home) == ["when-ci"]


def test_unpaired_intentions_are_unchanged(home):
    intentions.add(home, "genome", "again", note="n", due=50.0)
    intentions.add(home, "genome", "when-ci", note="n", when={"signal": "ci", "equals": "RED"})
    owed.advance(home, "genome", owed.check(home, "genome", now=60.0))
    assert _ids(home) == ["when-ci"]


def test_settle_pairs_fallback_and_signal_of_one_yield(tmp_path):
    from mishe_tauftauf import yielding
    from mishe_tauftauf.state_files import write_json
    store.register(tmp_path, "ci", max_age=600, subscribers=["genome"])
    store.observe(tmp_path, "ci", "GREEN", now=100.0, source="t")
    write_json(yielding.snapshot_path(tmp_path, "wake-9"),
               {"role": "genome", "snapshot": owed.check(tmp_path, "genome", now=100.0).to_json()})
    yielding.settle(tmp_path, "genome", "wake-9", text="wall", result="changed", now=100.0,
                    again=500.0, when={"signal": "ci", "equals": "RED"},
                    wall_writer=lambda home, role, text: None)
    pairs = {i["id"]: i.get("pair") for i in intentions.list_(tmp_path, "genome")}
    assert pairs["again"] == "wake-9" and pairs["when-ci"] == "wake-9"
    store.observe(tmp_path, "ci", "RED", now=101.0, source="t")
    owed.advance(tmp_path, "genome", owed.check(tmp_path, "genome", now=101.0))
    assert "when-ci" not in _ids(tmp_path) and "again" not in _ids(tmp_path)


def test_settle_warns_when_the_awaited_signal_is_not_registered(tmp_path):
    from mishe_tauftauf import yielding
    from mishe_tauftauf.state_files import write_json
    write_json(yielding.snapshot_path(tmp_path, "wake-10"),
               {"role": "genome", "snapshot": owed.check(tmp_path, "genome", now=100.0).to_json()})
    msg = yielding.settle(tmp_path, "genome", "wake-10", text="wall", result="changed", now=100.0,
                          when={"signal": "no-such", "equals": "RED"}, wall_writer=lambda h, r, t: None)
    assert "WARNING" in msg and "no-such" in msg


def test_settle_when_without_again_sets_a_paired_deadline(tmp_path):
    from mishe_tauftauf import yielding
    from mishe_tauftauf.state_files import write_json
    store.register(tmp_path, "ci", max_age=600, subscribers=["genome"])
    store.observe(tmp_path, "ci", "GREEN", now=100.0, source="t")
    write_json(yielding.snapshot_path(tmp_path, "wake-11"),
               {"role": "genome", "snapshot": owed.check(tmp_path, "genome", now=100.0).to_json()})
    msg = yielding.settle(tmp_path, "genome", "wake-11", text="wall", result="changed", now=100.0,
                          when={"signal": "ci", "equals": "RED"}, wall_writer=lambda h, r, t: None)
    pairs = {i["id"]: i.get("pair") for i in intentions.list_(tmp_path, "genome")}
    assert pairs["again"] == "wake-11" and pairs["when-ci"] == "wake-11"
    assert "deadline was set" in msg
