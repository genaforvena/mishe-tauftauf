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
    assert _again_due(home, "field-review") == 1000 + yielding.CLAMP_SECONDS
    assert "clamped --again from 43200s to 2h" in msg             # the clamp is printed in the result


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
