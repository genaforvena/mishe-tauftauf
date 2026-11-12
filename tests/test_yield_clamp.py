"""DM 300152: a role with an open lane cannot sleep past 2 h; a long wait with no subscribed signal warns;
field-review is a default subscriber of the moving-pin signal.

The 2026-11-12 dormancy case: field-review yielded to a 22 h 37 min wait with "Signals: none subscribed"
while it had unread work and no signal to wake it.
"""
from mishe_tauftauf import intentions, owed, signal_store, yielding
from mishe_tauftauf.state_files import write_json


def _open(home, role, key):
    write_json(yielding.snapshot_path(home, key), dict(role=role, snapshot=owed.Snapshot().to_json()))


def _settle(home, role, key, now, **kw):
    _open(home, role, key)
    return yielding.settle(home, role, key, text="Work", result="changed", now=now,
                           wall_writer=lambda *a: None, **kw)


def _again_due(home, role):
    return {e["id"]: e["due"] for e in intentions.list_(home, role) if "due" in e}["again"]


def test_open_inbox_clamps_a_long_again_to_two_hours(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    owed.touch_inbox(home, "field-review")                       # unread mail: an open lane
    msg = _settle(home, "field-review", "wake-f-1", 1000, again=12 * 3600)
    assert _again_due(home, "field-review") == 1000 + 12 * 3600   # the explicit deadline is kept whole
    recheck = {e["id"]: e["due"] for e in intentions.list_(home, "field-review") if "due" in e}
    assert recheck["again-recheck"] == 1000 + yielding.CLAMP_SECONDS
    assert "clamped --again from 43200s to 2h" in msg             # the clamp is printed in the result


def test_clamp_recheck_does_not_retire_the_paired_deadline(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    owed.touch_inbox(home, "field-review")
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"], now=10)
    _settle(home, "field-review", "wake-f-2", 1000, again=6 * 3600,
            when={"signal": "moving-pin-delivery", "equals": "VERIFIED"})
    # The 2h recheck fires first: consuming it must not take the 6h deadline or the signal condition with it.
    snap = owed.Snapshot(); snap.consumed = ["again-recheck"]
    owed.advance(home, "field-review", snap)
    left = {e["id"] for e in intentions.list_(home, "field-review")}
    assert "again" in left and "when-moving-pin-delivery" in left
    assert "again-recheck" not in left


def test_repeated_pin_value_is_an_edge_only_under_a_new_event(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"], now=10)
    assert signal_store.observe(home, "moving-pin-delivery", "VERIFIED", now=10, source="t", event="p1") is True
    assert signal_store.observe(home, "moving-pin-delivery", "VERIFIED", now=20, source="t", event="p1") is False
    rev = signal_store.rev(home, "moving-pin-delivery")
    assert signal_store.observe(home, "moving-pin-delivery", "VERIFIED", now=30, source="t", event="p2") is True
    assert signal_store.rev(home, "moving-pin-delivery") == rev + 1   # the subscriber's cursor sees a new rev


def test_ready_work_clamps_the_idle_wait_too(tmp_path, monkeypatch):
    home = tmp_path / "site"; home.mkdir()
    # A ready task needs a declared role to be saved; the lane check itself is what this test pins.
    monkeypatch.setattr(yielding, "open_lane", lambda home, role: True)
    msg = _settle(home, "docs", "wake-d-1", 1000, idle_seconds=10 * 3600)
    idle = {e["id"]: e["due"] for e in intentions.list_(home, "docs") if "due" in e}[yielding.IDLE_ID]
    assert idle == 1000 + yielding.CLAMP_SECONDS
    assert "clamped idle" in msg


def test_no_open_lane_keeps_the_requested_wait(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    signal_store.register(home, "ci", max_age=900, subscribers=["genome"], reoffer=3600, now=10)
    msg = _settle(home, "genome", "wake-g-1", 1000, again=12 * 3600)
    assert _again_due(home, "genome") == 1000 + 12 * 3600
    assert "clamped" not in msg and "WARNING" not in msg


def test_long_wait_with_no_subscribed_signal_warns(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    msg = _settle(home, "pub", "wake-p-1", 1000, again=22 * 3600)
    assert "subscribes to no signal" in msg                       # nothing can wake it before the timer


def test_field_review_is_a_default_subscriber_of_the_pin_signal(tmp_path):
    home = tmp_path / "site"; home.mkdir()
    # The live registration names only genome; the default must still reach field-review.
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["genome"], now=10)
    assert "field-review" in signal_store.registry(home)["moving-pin-delivery"]["subscribers"]
    assert "genome" in signal_store.registry(home)["moving-pin-delivery"]["subscribers"]
    assert "field-review" not in signal_store.registry(home).get("ci", {}).get("subscribers", [])


def test_successive_rechecks_reach_the_paired_deadline_under_an_open_lane(tmp_path, monkeypatch):
    # DM 300168: consuming a recheck with no --again must re-arm the next one from the pending deadline.
    home = tmp_path / "site"; home.mkdir()
    monkeypatch.setattr(yielding, "open_lane", lambda home, role: True)
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["genome"], now=10)
    _settle(home, "genome", "wake-s-1", 1000, again=6 * 3600,
            when={"signal": "moving-pin-delivery", "equals": "VERIFIED"})
    _settle(home, "genome", "wake-s-2", 2000)                      # inbox wake keeps the recheck (t=8200)
    for now, key in ((8200, "wake-s-3"), (15400, "wake-s-4")):
        snap = owed.Snapshot(); snap.consumed = ["again-recheck"]  # the recheck fires and is consumed
        owed.advance(home, "genome", snap)
        _settle(home, "genome", key, now)                          # yield with no --again while the lane is open
        due = {e["id"]: e["due"] for e in intentions.list_(home, "genome") if "due" in e}
        assert due["again"] == 1000 + 6 * 3600                     # the deadline is never moved by a recheck
        assert due["again-recheck"] == min(now + yielding.CLAMP_SECONDS, 1000 + 6 * 3600)
    assert due["again-recheck"] == 1000 + 6 * 3600                 # the last recheck lands on the deadline


def test_unrelated_yield_keeps_a_pending_recheck_while_the_lane_is_open(tmp_path, monkeypatch):
    home = tmp_path / "site"; home.mkdir()
    monkeypatch.setattr(yielding, "open_lane", lambda home, role: True)
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["genome"], now=10)
    _settle(home, "genome", "wake-g-3", 1000, again=6 * 3600,
            when={"signal": "moving-pin-delivery", "equals": "VERIFIED"})
    # An inbox wake yields with no --again: the pending 2h recheck must survive it.
    _settle(home, "genome", "wake-g-4", 2000)
    due = {e["id"]: e["due"] for e in intentions.list_(home, "genome") if "due" in e}
    assert due["again-recheck"] == 1000 + yielding.CLAMP_SECONDS
    assert due["again"] == 1000 + 6 * 3600
    # Once the lane closes, the same kind of yield retires the recheck.
    monkeypatch.setattr(yielding, "open_lane", lambda home, role: False)
    _settle(home, "genome", "wake-g-5", 3000)
    assert "again-recheck" not in {e["id"] for e in intentions.list_(home, "genome")}
