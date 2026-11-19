"""Controlled-failure tests for the post-move observation of the live consumers (pin_observation)."""
import time
import pytest

from mishe_tauftauf import pin_observation as po


def _site(tmp_path, *, point_at=None):
    home = tmp_path / "site"
    release = home / "releases" / ("a" * 40)
    (release / "src").mkdir(parents=True)
    (home / "runtime").mkdir()
    (home / "runtime" / "current").symlink_to(point_at or release)
    return home, release


def test_pointer_must_name_the_release_the_probe_runs_from(tmp_path):
    home, release = _site(tmp_path)
    assert po.check_pointer(home, release) == home / "runtime" / "current"
    other = home / "releases" / ("b" * 40)
    other.mkdir()
    with pytest.raises(ValueError, match="does not name this release"):
        po.check_pointer(home, other)


def test_missing_pointer_is_an_error_not_a_pass(tmp_path):
    home = tmp_path / "site"
    (home / "runtime").mkdir(parents=True)
    release = home / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    with pytest.raises(ValueError):
        po.check_pointer(home, release)


def test_wrappers_must_name_the_stable_pointer_and_no_sha(tmp_path):
    home, release = _site(tmp_path)
    stable = home / "runtime" / "current"
    (home / "bin").mkdir()
    for name in ("mishe", "mishe-tauftauf"):
        (home / "bin" / name).write_text(f"exec {stable}/bin/x\n")
    po.check_wrappers(home, stable, release.name)
    (home / "bin" / "mishe").write_text(f"exec {release}/bin/x {release.name}\n")
    with pytest.raises(ValueError, match="stable pointer alone"):
        po.check_wrappers(home, stable, release.name)


def test_drain_waits_for_old_invocations_and_fails_closed_when_they_stay(tmp_path):
    calls = []

    def pending(record, rows):
        calls.append(record["cutoff_us"])
        return [{"unit": "mishe-x.service", "invocation": "old", "start_us": 1, "pids": [1]}]

    with pytest.raises(ValueError, match="old consumer invocations remain"):
        po.drain_old(tmp_path, 5, timeout=0.0, sleep=lambda s: None, inventory=lambda h: {}, pending_old=pending)
    assert calls == [5]


def test_drain_timeout_reports_the_real_pending_unit_not_only_the_last_transient(tmp_path):
    reads = iter([
        [{"unit": "mishe-real.service", "invocation": "old", "start_us": 1, "pids": [1]}],
        ValueError("active cgroup evidence UNKNOWN: mishe-transient.service"),
    ])

    def pending(record, rows):
        item = next(reads)
        if isinstance(item, Exception):
            raise item
        return item

    # the first read sees the real unit; the sleep spends the deadline so the transient read is the last one
    with pytest.raises(ValueError) as caught:
        po.drain_old(tmp_path, 5, timeout=0.05, sleep=lambda s: time.sleep(0.06), inventory=lambda h: {},
                     pending_old=pending)
    message = str(caught.value)
    assert "mishe-real.service" in message
    assert "mishe-transient.service" in message


def test_drain_returns_when_nothing_old_remains_and_ignores_its_own_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "self")
    rows = iter([[{"unit": "u", "invocation": "self"}], []])
    assert po.drain_old(tmp_path, 5, timeout=5, sleep=lambda s: None, inventory=lambda h: {},
                        pending_old=lambda r, i: next(rows)) == 0


def test_a_failed_evidence_read_is_not_retried_as_transient(tmp_path):
    def pending(record, rows):
        raise ValueError("unbounded pinned consumer: mishe-x.service")

    with pytest.raises(ValueError, match="unbounded pinned consumer"):
        po.drain_old(tmp_path, 5, timeout=5, sleep=lambda s: None, inventory=lambda h: {}, pending_old=pending)


def test_a_pending_reload_that_clears_a_moment_later_is_not_a_failure(tmp_path, monkeypatch):
    home, release = _site(tmp_path)
    stable = home / "runtime" / "current"
    unit = home / "u" / "mishe-x.service"
    unit.parent.mkdir()
    unit.write_text("[Service]\nEnvironment=PYTHONPATH=" + str(stable / "src") + "\n")
    monkeypatch.setattr(po.rp, "config_paths", lambda h: [unit])
    states = iter(["yes", "no"])

    def show(name, *props):
        return {"LoadState": "loaded", "FragmentPath": str(unit), "Type": "oneshot", "NeedDaemonReload": next(states),
                "Environment": "PYTHONPATH=" + str(stable / "src")}

    assert po.check_units(home, stable, show=show, sleep=lambda s: None) == ["mishe-x.service"]


def test_a_reload_that_never_clears_is_a_failure(tmp_path, monkeypatch):
    home, release = _site(tmp_path)
    stable = home / "runtime" / "current"
    unit = home / "u" / "mishe-x.service"
    unit.parent.mkdir()
    unit.write_text("[Service]\nEnvironment=PYTHONPATH=" + str(stable / "src") + "\n")
    monkeypatch.setattr(po.rp, "config_paths", lambda h: [unit])

    def show(name, *props):
        return {"LoadState": "loaded", "FragmentPath": str(unit), "Type": "oneshot", "NeedDaemonReload": "yes",
                "Environment": "PYTHONPATH=" + str(stable / "src")}

    with pytest.raises(ValueError, match="pending daemon-reload"):
        po.check_units(home, stable, show=show, sleep=lambda s: None, reload=lambda: None)


def test_a_persistent_pending_reload_is_cleared_by_one_reload_then_judged_on_the_pins(tmp_path, monkeypatch):
    home, release = _site(tmp_path)
    stable = home / "runtime" / "current"
    unit = home / "u" / "mishe-x.service"
    unit.parent.mkdir()
    unit.write_text("[Service]\nEnvironment=PYTHONPATH=" + str(stable / "src") + "\n")
    monkeypatch.setattr(po.rp, "config_paths", lambda h: [unit])
    reloaded = []

    def show(name, *props):
        return {"LoadState": "loaded", "FragmentPath": str(unit), "Type": "oneshot",
                "NeedDaemonReload": "no" if reloaded else "yes", "Environment": "PYTHONPATH=" + str(stable / "src")}

    assert po.check_units(home, stable, show=show, sleep=lambda s: None, reload=lambda: reloaded.append(1)) == ["mishe-x.service"]
    assert reloaded == [1]


def test_after_a_reload_a_wrong_loaded_pin_is_still_a_failure(tmp_path, monkeypatch):
    home, release = _site(tmp_path)
    stable = home / "runtime" / "current"
    unit = home / "u" / "mishe-x.service"
    unit.parent.mkdir()
    unit.write_text("[Service]\nEnvironment=PYTHONPATH=" + str(stable / "src") + "\n")
    monkeypatch.setattr(po.rp, "config_paths", lambda h: [unit])

    def show(name, *props):
        return {"LoadState": "loaded", "FragmentPath": str(unit), "Type": "oneshot", "NeedDaemonReload": "no",
                "Environment": "PYTHONPATH=/elsewhere/src"}

    with pytest.raises(ValueError, match="loaded pin differs"):
        po.check_units(home, stable, show=show, sleep=lambda s: None, reload=lambda: None)


def test_observe_rearms_after_the_drain_and_before_judging_future_consumers(tmp_path, monkeypatch):
    """A due handler that started before the move re-arms the timer at exit, with its own release.

    The activation's arm therefore has to be repeated after the drain: otherwise the stale timer is
    the last writer and verify_future fails a correct pin move (2026-11-18 08:06Z f8004457 rollback).
    """
    home, release = _site(tmp_path)
    calls = []
    monkeypatch.setattr(po, "check_pointer", lambda h, r: calls.append("pointer") or home / "runtime" / "current")
    monkeypatch.setattr(po, "check_units", lambda h, s: calls.append("units") or [])
    monkeypatch.setattr(po, "check_wrappers", lambda h, s, sha: calls.append("wrappers"))
    monkeypatch.setattr(po.rp, "probe_problems", lambda h: calls.append("probe") or [])
    monkeypatch.setattr(po, "drain_old", lambda h, cutoff: calls.append(("drain", cutoff)) or 0)
    from mishe_tauftauf import work_boundary
    monkeypatch.setattr(work_boundary, "invoke_arm_consumer",
                        lambda expected: calls.append(("arm", expected)) or {})
    monkeypatch.setattr(po.fence, "verify_future",
                        lambda h, expected, previous: calls.append(("verify", list(calls))) or [])

    result = po.observe(home, release, cutoff_us=17)

    assert [c if isinstance(c, str) else c[0] for c in calls] == ["pointer", "units", "wrappers", "probe", "drain", "arm", "verify"]
    assert calls[4] == ("drain", 17)
    assert calls[5] == ("arm", home / "runtime" / "current" / "src")
    assert result == {"release": release.name, "units": [], "rearm": {}, "future_consumers": []}


def test_observe_without_a_cutoff_does_not_arm(tmp_path, monkeypatch):
    home, release = _site(tmp_path)
    calls = []
    monkeypatch.setattr(po, "check_pointer", lambda h, r: home / "runtime" / "current")
    monkeypatch.setattr(po, "check_units", lambda h, s: [])
    monkeypatch.setattr(po, "check_wrappers", lambda h, s, sha: None)
    monkeypatch.setattr(po.rp, "probe_problems", lambda h: [])
    from mishe_tauftauf import work_boundary
    monkeypatch.setattr(work_boundary, "invoke_arm_consumer", lambda expected: calls.append(expected) or {})
    monkeypatch.setattr(po.fence, "verify_future", lambda h, expected, previous: [])

    po.observe(home, release, cutoff_us=None)

    assert calls == []


def test_future_observation_waits_for_old_service_started_after_cutoff(tmp_path, monkeypatch):
    stable = tmp_path / "current" / "src"
    old = {"mishe-next-due-old.service": {"ActiveState": "active", "Environment": "PYTHONPATH=/old/src"}}
    new = {"mishe-next-due-new.service": {"ActiveState": "inactive", "pending_timer": True,
                                           "Environment": "PYTHONPATH=" + str(stable)}}
    samples = iter([old, new])
    monkeypatch.setattr(po.fence, "inventory", lambda home, previous: next(samples))
    result = po.wait_future(tmp_path, stable, timeout=5, sleep=lambda _: None)
    assert [row["unit"] for row in result] == ["mishe-next-due-new.service"]


def test_future_observation_rejects_wrong_queued_timer_without_waiting(tmp_path, monkeypatch):
    row = {"mishe-next-due-old.service": {"ActiveState": "inactive", "pending_timer": True,
                                           "Environment": "PYTHONPATH=/old/src"}}
    monkeypatch.setattr(po.fence, "inventory", lambda home, previous: row)
    with pytest.raises(ValueError, match="wrong pin"):
        po.wait_future(tmp_path, tmp_path / "new", timeout=5, sleep=lambda _: pytest.fail("must not wait"))


def test_future_observation_rejects_stale_timer_after_stale_service_exits(tmp_path, monkeypatch):
    samples = iter([
        {"old.service": {"ActiveState": "active", "Environment": "PYTHONPATH=/old/src"}},
        {"old.service": {"ActiveState": "inactive", "pending_timer": True,
                         "Environment": "PYTHONPATH=/old/src"}},
    ])
    monkeypatch.setattr(po.fence, "inventory", lambda home, previous: next(samples))
    with pytest.raises(ValueError, match="wrong pin"):
        po.wait_future(tmp_path, tmp_path / "new", timeout=5, sleep=lambda _: None)


def test_future_observation_does_not_hide_a_broken_inventory(tmp_path, monkeypatch):
    def broken(home, previous):
        raise ValueError("inventory UNKNOWN")

    monkeypatch.setattr(po.fence, "inventory", broken)
    with pytest.raises(ValueError, match="inventory UNKNOWN"):
        po.wait_future(tmp_path, tmp_path / "new", timeout=5, sleep=lambda _: pytest.fail("must not wait"))


@pytest.mark.parametrize("environment", ["PYTHONPATH=/old/src", ""])
def test_future_observation_fails_closed_if_active_pin_stays_wrong_or_unknown(tmp_path, monkeypatch, environment):
    row = {"mishe-next-due-old.service": {"ActiveState": "active", "Environment": environment}}
    monkeypatch.setattr(po.fence, "inventory", lambda home, previous: row)
    with pytest.raises(ValueError, match="wrong pin"):
        po.wait_future(tmp_path, tmp_path / "new", timeout=0, sleep=lambda _: None)


@pytest.mark.parametrize("active_first", [True, False])
def test_future_observation_rejects_a_queued_timer_masked_by_an_active_row(tmp_path, monkeypatch, active_first):
    """A queued wrong-pin row is fatal even when an active row precedes it and both vanish next poll."""
    active = ("a-active.service", {"ActiveState": "active", "Environment": "PYTHONPATH=/old/src"})
    queued = ("z-queued.service", {"ActiveState": "inactive", "pending_timer": True,
                                   "Environment": "PYTHONPATH=/old/src"})
    rows = dict([active, queued] if active_first else [queued, active])
    samples = iter([rows, {}])  # both wrong-pin rows disappear before a second poll could happen
    monkeypatch.setattr(po.fence, "inventory", lambda home, previous: next(samples))
    with pytest.raises(ValueError, match="z-queued.service") as caught:
        po.wait_future(tmp_path, tmp_path / "new", timeout=5, sleep=lambda _: pytest.fail("must not wait"))
    assert "wrong pin" in str(caught.value)
