"""A re-offer intention must not outlive the fault it was minted for.

``settle`` mints ``reoffer-<name>`` while a subscribed signal is not clear; before this
it never retired it when the signal recovered, so a healthy signal kept firing a
spurious wake (2026-11-11: genome's ``reoffer-view-readability`` and
``reoffer-observer-checks`` both sat under OK/GREEN).
"""
import pytest
from mishe_tauftauf import dispositions, intentions, owed, signal_store, yielding
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


def test_known_disposition_swaps_the_reoffer_for_its_recheck(tmp_path):
    # A resolver's expiring answer stops the hourly re-send; the watcher asks again at recheck_at
    # (2026-11-17: household-phones and budget re-offered hourly while both were dispositioned).
    home = tmp_path / "site"; home.mkdir()
    signal_store.register(home, "standing-fault", max_age=900, subscribers=["genome"], reoffer=3600, now=10)
    signal_store.observe(home, "standing-fault", "RED", now=10, source="test")
    _open(home, "genome", "wake-g-1"); _settle(home, "genome", "wake-g-1", 15)
    assert "reoffer-standing-fault" in _ids(home, "genome")
    dispositions.set_disposition(home, "standing-fault", "known", "resolver", "evidence", ttl=3600, now=20)
    _open(home, "genome", "wake-g-2"); _settle(home, "genome", "wake-g-2", 30)
    assert "reoffer-standing-fault" not in _ids(home, "genome")
    due = {e["id"]: e["due"] for e in intentions.list_(home, "genome") if "due" in e}
    assert due["disposition-standing-fault"] == 20 + 3600


def test_expired_disposition_brings_the_reoffer_back(tmp_path):
    # No open-ended mute: once the answer expires the finding is re-sent, so a stale judgement
    # never hides a standing fault.
    home = tmp_path / "site"; home.mkdir()
    signal_store.register(home, "standing-fault", max_age=900, subscribers=["genome"], reoffer=3600, now=10)
    signal_store.observe(home, "standing-fault", "RED", now=10, source="test")
    dispositions.set_disposition(home, "standing-fault", "known", "resolver", "evidence", ttl=5, now=10)
    _open(home, "genome", "wake-g-1"); _settle(home, "genome", "wake-g-1", 20)
    ids = _ids(home, "genome")
    assert "reoffer-standing-fault" in ids
    assert "disposition-standing-fault" not in ids


def _budget(home, now, remaining=0.9, age=0, reset_at=None):
    reset_at = now + 3600 if reset_at is None else reset_at
    w = {n: dict(state="FRESH", observed_at=now - age, remaining=remaining, reset_at=reset_at) for n in ("5h", "7d")}
    write_json(home / "engine" / "budget.json", dict(accounts=dict(a=dict(state="FRESH", windows=w))))


def _lane(home, role, state="ready"):
    from mishe_tauftauf import work_state
    (home / "charters").mkdir(exist_ok=True); (home / "charters" / f"{role}.md").write_text("c")
    work_state.save(home, role, dict(version=1, initiative="x", role=role, tasks=[dict(id="t", deliverable="d", state=state)]))


def _idle_due(home, role):
    return {e["id"]: e["due"] for e in intentions.list_(home, role) if "due" in e}["idle-initiative"]


def test_open_lane_with_spare_budget_returns_at_the_open_lane_pace(tmp_path):
    home = tmp_path / "site"; home.mkdir(); _budget(home, 1000); _lane(home, "genome")
    _open(home, "genome", "w1"); _settle(home, "genome", "w1", 1000)
    assert _idle_due(home, "genome") == 1000 + yielding.OPEN_LANE_SECONDS


def test_unknown_budget_or_no_lane_keeps_the_slow_pace(tmp_path):
    for case in ("stale", "stale-edge", "future", "future-1", "missing", "low"):
        home = tmp_path / case; home.mkdir()
        if case == "stale": _budget(home, 10000, age=7200)
        elif case == "stale-edge": _budget(home, 10000, age=yielding.BUDGET_MAX_AGE + 1)
        elif case == "future": _budget(home, 10000, age=-7200)
        elif case == "future-1": _budget(home, 10000, age=-1)
        elif case == "low": _budget(home, 10000, remaining=0.04)
        elif case != "missing": _budget(home, 10000)
        _lane(home, "genome")
        _open(home, "genome", "w1"); _settle(home, "genome", "w1", 10000)
        assert _idle_due(home, "genome") == 10000 + yielding.DEFAULT_IDLE_SECONDS, case


def test_spare_budget_is_false_at_and_after_the_window_reset(tmp_path):
    # Reset-boundary experiment (wall DM 300130/300135): observed_at fresh, reset_at=1000.
    # Before this, spare_budget read True at 999, 1000 and 1001, so idle_delay scheduled the open-lane wake on expired evidence.
    home = tmp_path / "site"; home.mkdir(); _budget(home, 999, reset_at=1000)
    assert yielding.spare_budget(home, 999) is True
    assert yielding.spare_budget(home, 1000) is False
    assert yielding.spare_budget(home, 1001) is False
    _lane(home, "genome")
    assert yielding.idle_delay(home, "genome", 1000, yielding.DEFAULT_IDLE_SECONDS) == yielding.DEFAULT_IDLE_SECONDS


def _free_budget(home, now, age=0, **changes):
    row = dict(free=True, in_routes=True, verdict="FREE", observed_at=now - age)
    row.update(changes)
    write_json(home / "engine" / "budget.json",
               dict(accounts={}, free_models={"omp:opencode-go/hy4-flash-free:-": row}))


def test_open_lane_with_a_fresh_free_route_returns_in_thirty_minutes(tmp_path):
    # hy4-flash has no account window, so spare_budget is False while the route is live in routes.json.
    home = tmp_path / "site"; home.mkdir(); _free_budget(home, 1000); _lane(home, "genome")
    _open(home, "genome", "w1"); _settle(home, "genome", "w1", 1000)
    assert yielding.spare_budget(home, 1000) is False
    assert yielding.fresh_free_route(home, 1000) is True
    assert _idle_due(home, "genome") == 1000 + yielding.OPEN_LANE_SECONDS


@pytest.mark.parametrize("changes,age", [
    ({"in_routes": False}, 0),
    ({"verdict": "RETIRED"}, 0),
    ({"free": False}, 0),
    ({}, yielding.BUDGET_MAX_AGE + 1),
    ({}, -1),
])
def test_free_route_that_is_not_live_keeps_the_slow_pace(tmp_path, changes, age):
    home = tmp_path / "site"; home.mkdir(); _free_budget(home, 10000, age=age, **changes); _lane(home, "genome")
    _open(home, "genome", "w1"); _settle(home, "genome", "w1", 10000)
    assert yielding.fresh_free_route(home, 10000) is False
    assert _idle_due(home, "genome") == 10000 + yielding.DEFAULT_IDLE_SECONDS


def test_idle_role_with_spare_budget_uses_the_shorter_idle_pace_and_without_spare_the_slow_one(tmp_path):
    spare, tight = tmp_path / "spare", tmp_path / "tight"
    spare.mkdir(); tight.mkdir(); _budget(spare, 1000); _budget(tight, 1000, remaining=0.04)
    for home in (spare, tight):
        _lane(home, "genome", state="done")                                      # no ready work: idle initiative only
        _open(home, "genome", "w1"); _settle(home, "genome", "w1", 1000)
    assert _idle_due(spare, "genome") == 1000 + yielding.SPARE_IDLE_SECONDS
    assert _idle_due(tight, "genome") == 1000 + yielding.DEFAULT_IDLE_SECONDS
    assert yielding.OPEN_LANE_SECONDS < yielding.SPARE_IDLE_SECONDS < yielding.DEFAULT_IDLE_SECONDS


def test_spare_floors_leave_room_to_spend_but_not_the_last_of_a_window(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    _budget(home, 1000, remaining=0.12)
    assert yielding.spare_budget(home, 1000) is True                             # 5h 12% left >= 10%, 7d 12% >= 5%
    w = {"5h": dict(state="FRESH", observed_at=1000, remaining=0.5, reset_at=4600),
         "7d": dict(state="FRESH", observed_at=1000, remaining=0.04, reset_at=4600)}
    write_json(home / "engine" / "budget.json", dict(accounts=dict(a=dict(state="FRESH", windows=w))))
    assert yielding.spare_budget(home, 1000) is False                            # the weekly window is spent


def test_settle_survives_a_truncated_budget_file(tmp_path):
    # 2026-11-21 review of 4346a39f: a truncated engine/budget.json raised out of settlement after the
    # wall was written and the cursor advanced, so no receipt or idle intention was left. Bad budget
    # evidence reads as no spare budget and the role still settles on the 2 h pace.
    home = tmp_path / "site"; (home / "engine").mkdir(parents=True)
    (home / "engine" / "budget.json").write_text("{", encoding="utf-8")
    _open(home, "health", "wake-h-9")
    yielding.settle(home, "health", "wake-h-9", text="Work", result="changed", now=100, wall_writer=lambda *a: None)
    assert yielding.receipt_path(home, "wake-h-9").exists()
    idle = [e for e in intentions.list_(home, "health") if e.get("id") == yielding.IDLE_ID]
    assert idle and float(idle[0]["due"]) == 100 + yielding.DEFAULT_IDLE_SECONDS
