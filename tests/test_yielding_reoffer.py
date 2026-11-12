"""A re-offer intention must not outlive the fault it was minted for.

``settle`` mints ``reoffer-<name>`` while a subscribed signal is not clear; before this
it never retired it when the signal recovered, so a healthy signal kept firing a
spurious wake (2026-11-11: genome's ``reoffer-view-readability`` and
``reoffer-observer-checks`` both sat under OK/GREEN).
"""
from mishe_tauftauf import intentions, owed, signal_store, yielding
from mishe_tauftauf.state_files import write_json


def _open(home, role, key):
    write_json(yielding.snapshot_path(home, key), dict(role=role, snapshot=owed.Snapshot().to_json()))


def _settle(home, role, key, now):
    yielding.settle(home, role, key, text="Work", result="changed", now=now, wall_writer=lambda *a: None)


def _ids(home, role):
    return {e["id"] for e in intentions.list_(home, role)}


def test_continue_yield_is_due_after_a_floor_not_at_once(tmp_path):
    # A due-now continue re-armed the next wake immediately (restart storm, 2026-11-11).
    home = tmp_path / "site"; home.mkdir()
    _open(home, "health", "wake-h-1")
    yielding.settle(home, "health", "wake-h-1", text="Work", result="changed", now=100,
                    continue_=True, wall_writer=lambda *a: None)
    due = {e["id"]: e["due"] for e in intentions.list_(home, "health") if "due" in e}
    assert due["continue"] == 100 + yielding.CONTINUE_FLOOR_SECONDS
    assert intentions.next_due(home) > 100


def test_settle_retires_the_reoffer_once_the_signal_is_clear(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    signal_store.register(home, "view-readability", max_age=900, subscribers=["genome"], reoffer=3600, now=10)
    signal_store.observe(home, "view-readability", "RED", now=10, source="test")
    _open(home, "genome", "wake-g-1"); _settle(home, "genome", "wake-g-1", 20)
    assert "reoffer-view-readability" in _ids(home, "genome")        # a standing fault offers itself back
    signal_store.observe(home, "view-readability", "GREEN", now=30, source="test")
    _open(home, "genome", "wake-g-2"); _settle(home, "genome", "wake-g-2", 40)
    assert "reoffer-view-readability" not in _ids(home, "genome")    # recovered: the re-offer is residue


def test_settle_keeps_the_reoffer_while_the_signal_is_unreadable(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    signal_store.register(home, "observer-checks", max_age=900, subscribers=["genome"], reoffer=3600, now=10)
    signal_store.observe_unknown(home, "observer-checks", now=10, reason="no run yet", source="test")
    _open(home, "genome", "wake-g-1"); _settle(home, "genome", "wake-g-1", 20)
    assert "reoffer-observer-checks" in _ids(home, "genome")
    _open(home, "genome", "wake-g-2"); _settle(home, "genome", "wake-g-2", 40)
    assert "reoffer-observer-checks" in _ids(home, "genome")         # missing evidence is not recovery


def _budget(home, now, remaining=0.9, age=0):
    w = {n: dict(state="FRESH", observed_at=now - age, remaining=remaining) for n in ("5h", "7d")}
    write_json(home / "engine" / "budget.json", dict(accounts=dict(a=dict(state="FRESH", windows=w))))


def _lane(home, role, state="ready"):
    from mishe_tauftauf import work_state
    (home / "charters").mkdir(exist_ok=True); (home / "charters" / f"{role}.md").write_text("c")
    work_state.save(home, role, dict(version=1, initiative="x", role=role, tasks=[dict(id="t", deliverable="d", state=state)]))


def _idle_due(home, role):
    return {e["id"]: e["due"] for e in intentions.list_(home, role) if "due" in e}["idle-initiative"]


def test_open_lane_with_spare_budget_returns_in_thirty_minutes(tmp_path):
    home = tmp_path / "site"; home.mkdir(); _budget(home, 1000); _lane(home, "genome")
    _open(home, "genome", "w1"); _settle(home, "genome", "w1", 1000)
    assert _idle_due(home, "genome") == 1000 + yielding.OPEN_LANE_SECONDS


def test_unknown_budget_or_no_lane_keeps_the_slow_pace(tmp_path):
    for case in ("stale", "stale-edge", "future", "future-1", "missing", "nolane", "low"):
        home = tmp_path / case; home.mkdir()
        if case == "stale": _budget(home, 10000, age=7200)
        elif case == "stale-edge": _budget(home, 10000, age=yielding.BUDGET_MAX_AGE + 1)
        elif case == "future": _budget(home, 10000, age=-7200)
        elif case == "future-1": _budget(home, 10000, age=-1)
        elif case == "low": _budget(home, 10000, remaining=0.05)
        elif case != "missing": _budget(home, 10000)
        if case != "nolane": _lane(home, "genome")
        else: _lane(home, "genome", state="done")
        _open(home, "genome", "w1"); _settle(home, "genome", "w1", 10000)
        assert _idle_due(home, "genome") == 10000 + yielding.DEFAULT_IDLE_SECONDS, case
