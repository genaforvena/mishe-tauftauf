"""The canonical adapter against the plant's own lifecycle.

These tests use the real tape, the real wall writer and the real settlement.
Only the tmux layer is replaced, because a turn needs a live pane to be
delivered to.
"""

import pytest

from mishe_tauftauf.core import (Dispatch, DispatchStore, Supervisor,
                                 SupervisorError)
from mishe_tauftauf.core.adapters import (CanonicalLiveObservation,
                                          CanonicalObservation,
                                          CanonicalRoleRegistry,
                                          CanonicalWorker)
from mishe_tauftauf.core.registry import StaticRoleRegistry


@pytest.fixture
def site(tmp_path):
    """A disposable site home with a tape, a wall and a declared role."""
    from mishe_tauftauf import cli
    home = tmp_path / "site"
    cli.initialize(home)
    (home / "charters").mkdir(exist_ok=True)
    (home / "charters" / "genome.md").write_text("# genome\n", encoding="utf-8")
    launcher = home / "minds" / "genome"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    return home


def stub_pane(monkeypatch, site, *, ready=True):
    """Make the tmux layer report an idle, owned pane with a live lease.

    The canonical tick reads three things before it will create a wake: the
    top pane's lease, the role's dashboard record and the mind's own
    readiness.  Each is stubbed with the shape the real reader accepts, so
    the wake and the settlement paths are the plant's own.
    """
    import hashlib
    from datetime import datetime, timezone
    from subprocess import CompletedProcess
    from mishe_tauftauf import seed, tmux

    stamp = datetime.now(timezone.utc).isoformat()
    lease = f"-- pane live {stamp} · refresh 0.1s · ticks every frame --"
    frame = "STATE: GREEN\nThe site is healthy.\n"
    pid = ["4242"]

    def fake_tmux(*args, **kwargs):
        argv = [str(part) for part in args]
        if "respawn-pane" in argv:
            pid[0] = "4243" if pid[0] == "4242" else "4244"
            return CompletedProcess(argv, 0, b"", b"")
        if "capture-pane" in argv:
            return CompletedProcess(argv, 0, (lease + "\n" + frame).encode("utf-8"), b"")
        if "display-message" in argv and "pane_current_command" in argv:
            return CompletedProcess(argv, 0, b"omp\n", b"")
        if "display-message" in argv and "#{pane_dead}" in argv:
            return CompletedProcess(argv, 0, b"0\n", b"")
        if "display-message" in argv and "#{pane_pid}" in argv:
            return CompletedProcess(argv, 0, (pid[0] + "\n").encode(), b"")
        if "display-message" in argv:
            return CompletedProcess(argv, 0, b"4242\n", b"")
        if "show-option" in argv:
            return CompletedProcess(argv, 0, str(site.resolve()).encode("utf-8"), b"")
        if "has-session" in argv:
            return CompletedProcess(argv, 0, b"", b"")
        if "list-panes" in argv:
            return CompletedProcess(argv, 0, b"0 4242\n", b"")
        return CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(seed, "_tmux", fake_tmux)
    monkeypatch.setattr(tmux, "owns_session", lambda *a, **k: True)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a, **k: ready)
    monkeypatch.setattr(seed, "_mind_idle", lambda *a, **k: True)
    # The tick reads the top pane and its liveness through the tmux module.
    monkeypatch.setattr(tmux, "capture_raw", lambda session, slug: lease + "\n" + frame)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a, **k: False)
    monkeypatch.setattr(tmux, "lease_value", lambda text: lease)

    # The dashboard is the role's own frame, written the way the renderer
    # writes it, so the tick's own freshness check applies.
    import json
    (site / "dashboards").mkdir(exist_ok=True)
    (site / "dashboards" / "genome.json").write_text(json.dumps({
        "version": 1, "slug": "genome", "home": str(site.resolve()),
        "created": stamp, "frame": frame, "ok": True,
        "sha256": hashlib.sha256(frame.encode()).hexdigest(),
    }), encoding="utf-8")


def test_the_role_registry_reads_the_sites_declared_roles(site):
    registry = CanonicalRoleRegistry(site)
    assert "genome" in registry.roles()
    assert registry.knows("genome")
    assert not registry.knows("no-such-role")


def test_live_observation_bootstraps_and_does_not_hide_sensor_failures(site, monkeypatch):
    from mishe_tauftauf import dashboard, seed, tmux
    stub_pane(monkeypatch, site)
    live = CanonicalLiveObservation(home=site, session="mishe-trial", role="genome")
    assert seed._state(site, "genome")[4] is None
    assert live().fresh and live().healthy
    assert seed._state(site, "genome")[4] is None

    for frame in ("STATE: RED\n", "UNKNOWN sensor offline\n", "FAIL detector unavailable\n",
                  "STATE: GREEN\nSTALE — check report old\n"):
        monkeypatch.setattr(dashboard, "read", lambda *args, frame=frame: (frame, True))
        reading = live()
        assert not reading.healthy
    monkeypatch.setattr(dashboard, "read", lambda *args: ("STATE: GREEN\n", False))
    assert not live().fresh
    monkeypatch.setattr(tmux, "lease_value", lambda *args: None)
    assert not live().fresh


def test_new_only_rechecks_live_health_before_creating_wake(site, monkeypatch):
    from mishe_tauftauf import dashboard, seed
    stub_pane(monkeypatch, site)
    live = CanonicalLiveObservation(home=site, session="mishe-trial", role="genome")
    assert live().healthy
    monkeypatch.setattr(dashboard, "read", lambda *args: ("UNKNOWN detector unavailable\n", True))
    sent = []
    monkeypatch.setattr(seed, "_send", lambda *args: sent.append(args))
    receipt = seed.tick(site, "mishe-trial", "genome", new_only=True)
    assert receipt.startswith("UNKNOWN")
    assert seed._state(site, "genome")[1] is None
    assert not sent
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    supervisor = Supervisor(store=DispatchStore(site / "core-dispatch"),
                            registry=StaticRoleRegistry(["genome"]), worker=worker)
    result = supervisor.dispatch("genome", "sensor-race", discard_skipped_poll=True)
    assert result.state.name == "skipped"
    assert "UNKNOWN" in result.detail
    assert not supervisor.store.open_records()
    assert not sent


def test_stale_record_does_not_block_a_fresh_live_reading(site, monkeypatch):
    from mishe_tauftauf import seed
    stub_pane(monkeypatch, site)
    seed.Feed(site).append_record(
        "seed", "seed observation genome",
        {"role": "genome", "digest": "old", "snapshot": "STATE: GREEN",
         "command_ok": True, "notify": True}, kind="observation")
    monkeypatch.setattr(CanonicalObservation, "_age", staticmethod(lambda timestamp: 3600))
    assert not CanonicalObservation(home=site, role="genome")().fresh
    assert CanonicalLiveObservation(home=site, session="mishe-trial", role="genome")().healthy

def test_mind_settles_exact_delivered_wake(site, monkeypatch):
    """The real tick delivers; the receiving mind settles through seed."""
    import threading
    from mishe_tauftauf import seed, wall

    stub_pane(monkeypatch, site)
    monkeypatch.setattr(wall, "restore", lambda *args: "restore")
    clear_due = seed._clear_due
    monkeypatch.setattr(seed, "_clear_due", lambda home, role, grace: clear_due(home, role, 0))
    notes = site / "mind-notes"
    notes.write_text("The mind investigated the live evidence.\n", encoding="utf-8")
    threads = []
    failures = []

    def send(target, text):
        def mind():
            try:
                wake = seed._state(site, "genome")[1]
                seed.yield_wake(site, "genome", wake, notes, result="verified")
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=mind)
        threads.append(thread)
        thread.start()

    monkeypatch.setattr(seed, "_send", send)
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=5)
    supervisor = Supervisor(store=DispatchStore(site / "core-dispatch"),
                            registry=StaticRoleRegistry(["genome"]), worker=worker)
    record = supervisor.dispatch("genome", "attempt-1")
    for thread in threads:
        thread.join(timeout=5)
    assert not failures
    assert record.state.name == "completed", record.detail
    _, pending, last_yield, last_clear, *_ = seed._state(site, "genome")
    assert pending is None
    assert last_clear == last_yield
    assert record.detail == f"canonical wake {last_yield}: canonical settlement and idle clear observed"
    assert (site / "walls" / "genome.md").read_text() == notes.read_text()
    seed.Feed(site).append("operator", "[dm] to=genome\nAnother turn is needed.")
    second = supervisor.dispatch("genome", "attempt-2")
    for thread in threads:
        thread.join(timeout=5)
    assert not failures
    assert second.state.name == "completed", second.detail
    _, pending, second_yield, second_clear, *_ = seed._state(site, "genome")
    assert pending is None
    assert second_yield != last_yield
    assert second_clear == second_yield


def test_settlement_does_not_complete_while_mind_is_busy(site, monkeypatch):
    import threading
    from mishe_tauftauf import seed, wall
    stub_pane(monkeypatch, site)
    monkeypatch.setattr(wall, "restore", lambda *args: "restore")
    monkeypatch.setattr(seed, "_mind_idle", lambda *args: False)
    clear_due = seed._clear_due
    monkeypatch.setattr(seed, "_clear_due", lambda home, role, grace: clear_due(home, role, 0))
    notes = site / "mind-notes"
    notes.write_text("The mind recorded its actual handoff.\n", encoding="utf-8")
    threads = []

    def send(target, text):
        def settle():
            wake = seed._state(site, "genome")[1]
            seed.yield_wake(site, "genome", wake, notes, result="verified")
        thread = threading.Thread(target=settle)
        threads.append(thread)
        thread.start()

    monkeypatch.setattr(seed, "_send", send)
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=2)
    supervisor = Supervisor(store=DispatchStore(site / "core-dispatch"),
                            registry=StaticRoleRegistry(["genome"]), worker=worker)
    record = supervisor.dispatch("genome", "busy-attempt")
    for thread in threads:
        thread.join(timeout=5)
    _, pending, settled, cleared, *_ = seed._state(site, "genome")
    assert pending is None
    assert settled is not None and cleared != settled
    assert record.state.name == "unknown"
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "another-attempt")


def test_unsettled_delivery_blocks_new_dispatch(site, monkeypatch):
    from mishe_tauftauf import seed, wall
    stub_pane(monkeypatch, site)
    monkeypatch.setattr(wall, "restore", lambda *args: "restore")
    monkeypatch.setattr(seed, "_send", lambda *args: None)
    store = DispatchStore(site / "core-dispatch")
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=0.01)
    supervisor = Supervisor(store=store, registry=StaticRoleRegistry(["genome"]), worker=worker)
    record = supervisor.dispatch("genome", "attempt-1")
    pending = seed._state(site, "genome")[1]
    assert record.state.name == "unknown"
    assert f"canonical wake {pending}" in record.detail
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "attempt-2")
    assert seed._state(site, "genome")[1] == pending


def test_tick_race_with_existing_wake_does_not_redeliver(site, monkeypatch):
    """The final pending check is inside the canonical tick's seed lock."""
    from mishe_tauftauf import seed
    stub_pane(monkeypatch, site)
    real_tick = seed.tick
    sent = []
    monkeypatch.setattr(seed, "_send", lambda *args: sent.append(args))

    def racing_tick(home, session, role, interval, *, new_only):
        seed.Feed(home).append("seed", "seed wake genome observation=1\nAnother dispatcher.")
        return real_tick(home, session, role, interval, new_only=new_only)

    monkeypatch.setattr(seed, "tick", racing_tick)
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    result = worker.run(Dispatch(role="genome", key="attempt-1", payload={}))
    assert result.outcome == "unknown"
    assert "already pending" in result.detail
    assert sent == []


def test_uncertain_tick_effect_remains_open(site, monkeypatch):
    from mishe_tauftauf import seed
    def uncertain_tick(home, session, role, interval, *, new_only):
        seed.Feed(home).append("seed", "seed wake genome observation=1\nPossibly delivered.")
        raise RuntimeError("delivery outcome lost")
    monkeypatch.setattr(seed, "tick", uncertain_tick)
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    supervisor = Supervisor(store=DispatchStore(site / "core-dispatch"),
                            registry=StaticRoleRegistry(["genome"]), worker=worker)
    with pytest.raises(RuntimeError):
        supervisor.dispatch("genome", "attempt-1")
    assert supervisor.status("genome").state.name == "unknown"
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "attempt-2")


def test_process_crash_after_wake_leaves_started_and_blocks_replay(site):
    import subprocess
    import sys
    from pathlib import Path

    script = """
import os, sys
sys.path.insert(0, sys.argv[2])
from pathlib import Path
from mishe_tauftauf import seed
from mishe_tauftauf.core import DispatchStore, Supervisor, StaticRoleRegistry
from mishe_tauftauf.core.adapters import CanonicalWorker
home = Path(sys.argv[1])
def tick(home, session, role, interval, *, new_only):
    seed.Feed(home).append("seed", "seed wake genome observation=1\\nDelivery outcome unknown.")
    os._exit(23)
seed.tick = tick
worker = CanonicalWorker(home=home, session="mishe-trial", role="genome", timeout=1)
supervisor = Supervisor(store=DispatchStore(home / "core-dispatch"),
                        registry=StaticRoleRegistry(["genome"]), worker=worker)
supervisor.dispatch("genome", "crashed-attempt")
"""
    source = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run([sys.executable, "-c", script, str(site), str(source)], timeout=10)
    assert result.returncode == 23
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    supervisor = Supervisor(store=DispatchStore(site / "core-dispatch"),
                            registry=StaticRoleRegistry(["genome"]), worker=worker)
    assert supervisor.status("genome").state.name == "started"
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "next-attempt")


def test_existing_wake_is_never_retried(site, monkeypatch):
    from mishe_tauftauf import seed
    seed.Feed(site).append("seed", "seed wake genome observation=1\nExisting.")
    monkeypatch.setattr(seed, "tick", lambda *args, **kwargs: pytest.fail("redelivery"))
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    assert worker.run(Dispatch(role="genome", key="attempt-1", payload={})).outcome == "unknown"


def test_unknown_tick_is_not_skipped(site, monkeypatch):
    from mishe_tauftauf import seed
    monkeypatch.setattr(seed, "tick", lambda *args, **kwargs: "UNKNOWN seed genome pane unavailable")
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    assert worker.run(Dispatch(role="genome", key="attempt-1", payload={})).outcome == "unknown"


def test_waiting_tick_is_skipped_without_wake(site, monkeypatch):
    from mishe_tauftauf import seed
    monkeypatch.setattr(seed, "tick", lambda *args, **kwargs: "waiting seed genome wall stable")
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    assert worker.run(Dispatch(role="genome", key="attempt-1", payload={})).outcome == "skipped"


def test_unmatched_settlement_does_not_complete_dispatch(site, monkeypatch):
    from mishe_tauftauf import seed
    def tick(home, session, role, interval, *, new_only):
        feed = seed.Feed(home)
        wake = feed.append("seed", "seed wake genome observation=1\nActual wake.")
        feed.append("seed", f"seed yield genome wake={wake.sequence + 1}")
        return f"wake seed genome {wake.sequence}"
    monkeypatch.setattr(seed, "tick", tick)
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=0.01)
    result = worker.run(Dispatch(role="genome", key="attempt", payload={}))
    assert result.outcome == "unknown"
    assert "settlement or idle clear not observed" in result.detail



def test_competing_ticks_create_only_one_delivery(site, monkeypatch):
    import threading
    from mishe_tauftauf import seed, wall
    stub_pane(monkeypatch, site)
    monkeypatch.setattr(wall, "restore", lambda *args: "restore")
    sent = []
    monkeypatch.setattr(seed, "_send", lambda *args: sent.append(args))
    real_tick = seed.tick
    barrier = threading.Barrier(2)

    def simultaneous_tick(*args, **kwargs):
        barrier.wait(timeout=5)
        return real_tick(*args, **kwargs)

    monkeypatch.setattr(seed, "tick", simultaneous_tick)
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=0.01)
    results = []
    def run(key):
        results.append(worker.run(Dispatch(role="genome", key=key, payload={})))
    threads = [threading.Thread(target=run, args=(str(i),)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert all(result.outcome == "unknown" for result in results)
    assert len(sent) == 1


def test_reconcile_refuses_a_busy_or_unobservable_mind(site, monkeypatch):
    from mishe_tauftauf import seed, tmux
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    supervisor = Supervisor(store=DispatchStore(site / "core-dispatch"),
                            registry=StaticRoleRegistry(["genome"]), worker=worker)
    monkeypatch.setattr(seed, "tick", lambda *args, **kwargs: "UNKNOWN seed genome transport")
    assert supervisor.dispatch("genome", "attempt").state.name == "unknown"
    monkeypatch.setattr(tmux, "owns_session", lambda *args: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *args: False)
    with pytest.raises(ValueError, match="not observably idle"):
        supervisor.reconcile("genome", detail="checked the wake")
    assert supervisor.status("genome").state.name == "unknown"


def test_reconcile_refuses_settlement_without_idle_clear(site, monkeypatch):
    from mishe_tauftauf import seed, tmux

    wake = seed.Feed(site).append("seed", "seed wake genome observation=1\nInvestigate.")
    notes = site / "notes.md"
    notes.write_text("Checked the retained issue.", encoding="utf-8")
    seed.yield_wake(site, "genome", wake.sequence, notes, result="verified")
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome", timeout=1)
    supervisor = Supervisor(store=DispatchStore(site / "core-dispatch"),
                            registry=StaticRoleRegistry(["genome"]), worker=worker)
    assert supervisor.dispatch("genome", "attempt").state.name == "unknown"
    monkeypatch.setattr(tmux, "owns_session", lambda *args: True)
    monkeypatch.setattr(seed, "_mind_idle", lambda *args: True)
    with pytest.raises(ValueError, match="not cleared"):
        supervisor.reconcile("genome", detail="checked the wake")
    assert supervisor.status("genome").state.name == "unknown"


def test_the_observation_reads_the_canonical_state(site, monkeypatch):
    stub_pane(monkeypatch, site)
    # A role with no observation record has no evidence, so the reading is
    # UNKNOWN rather than healthy.
    reading = CanonicalObservation(home=site, role="genome")()
    assert reading.fresh is False
    assert reading.healthy is False
    assert "no observation" in reading.detail


def test_the_observation_is_healthy_when_the_role_has_an_observation(site, monkeypatch):
    from mishe_tauftauf import seed
    stub_pane(monkeypatch, site)
    # A real observation record for the role.
    seed.Feed(site).append_record("seed", "seed observation genome",
                                  {"digest": "a" * 64, "role": "genome",
                                   "snapshot": "STATE: GREEN", "command_ok": True,
                                   "notify": True}, kind="observation")
    reading = CanonicalObservation(home=site, role="genome")()
    assert reading.fresh is True
    assert reading.healthy is True
    assert "pending=none" in reading.detail




def test_the_observation_reports_an_unreadable_state_as_unknown(site, monkeypatch):
    from mishe_tauftauf import seed
    monkeypatch.setattr(seed, "_state", lambda *a, **k: (_ for _ in ()).throw(OSError("tape unreadable")))
    reading = CanonicalObservation(home=site, role="genome")()
    assert reading.fresh is False
    assert reading.healthy is False




def test_the_observation_is_not_healthy_for_the_plants_no_sensor_record(site, monkeypatch):
    """The plant's own no-evidence record is not a pass."""
    from mishe_tauftauf import seed
    stub_pane(monkeypatch, site)
    seed.Feed(site).append_record("seed", "seed observation genome",
                                  {"digest": "b" * 64, "role": "genome",
                                   "snapshot": "UNKNOWN - no sensor observation; "
                                               "addressed escalation only.",
                                   "command_ok": False, "notify": True},
                                  kind="observation")
    reading = CanonicalObservation(home=site, role="genome")()
    assert reading.healthy is False


def test_the_observation_is_not_healthy_for_a_red_sensor(site, monkeypatch):
    """A sensor that reports RED is not a pass."""
    from mishe_tauftauf import seed
    stub_pane(monkeypatch, site)
    seed.Feed(site).append_record("seed", "seed observation genome",
                                  {"digest": "c" * 64, "role": "genome",
                                   "snapshot": "STATE: RED - the sensor is failing",
                                   "command_ok": True, "notify": True},
                                  kind="observation")
    reading = CanonicalObservation(home=site, role="genome")()
    assert reading.fresh is True
    assert reading.healthy is False




def test_a_stale_observation_is_not_healthy(site, monkeypatch):
    """An observation from an hour ago is not evidence about now."""
    from mishe_tauftauf import seed
    stub_pane(monkeypatch, site)
    seed.Feed(site).append_record("seed", "seed observation genome",
                                  {"digest": "d" * 64, "role": "genome",
                                   "snapshot": "STATE: GREEN\nAll good.",
                                   "command_ok": True, "notify": True},
                                  kind="observation")
    reading = CanonicalObservation(home=site, role="genome", max_age_seconds=0.0)()
    assert reading.fresh is False
    assert reading.healthy is False
    assert "stale" in reading.detail


def test_a_red_in_the_frame_body_is_not_healthy(site, monkeypatch):
    """The plant's verdict is in the body, not in the headline.

    The headline reads GREEN whenever the silence watcher is live, whatever
    the renderer below it says.  A frame whose body reports RED is a
    problem, and the plant's own pattern decides it.
    """
    from mishe_tauftauf import seed
    stub_pane(monkeypatch, site)
    seed.Feed(site).append_record("seed", "seed observation genome",
                                  {"digest": "e" * 64, "role": "genome",
                                   "snapshot": "HEADLINE: GREEN — mind ok; watcher live\n"
                                               "SERVICE mishe-self-development-current-coordination.service: RED inactive/dead restarts=0\n"
                                               "STATE: RED — service failure or empty manifest\n",
                                   "command_ok": True, "notify": True},
                                  kind="observation")
    reading = CanonicalObservation(home=site, role="genome")()
    assert reading.fresh is True
    assert reading.healthy is False
    assert "reports a problem" in reading.detail
