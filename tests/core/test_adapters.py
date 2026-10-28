"""The canonical adapter against the plant's own lifecycle.

These tests use the real tape, the real wall writer and the real settlement.
Only the tmux layer is replaced, because a turn needs a live pane to be
delivered to.
"""
import subprocess
import sys

import pytest

from mishe_tauftauf.core import Dispatch, DispatchStore, Supervisor
from mishe_tauftauf.core.adapters import (CanonicalObservation,
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

    def fake_tmux(*args, **kwargs):
        argv = [str(part) for part in args]
        if "capture-pane" in argv:
            return CompletedProcess(argv, 0, (lease + "\n" + frame).encode("utf-8"), b"")
        if "display-message" in argv and "pane_current_command" in argv:
            return CompletedProcess(argv, 0, b"omp\n", b"")
        if "display-message" in argv and "pane_dead" in argv:
            return CompletedProcess(argv, 0, b"0 4242\n", b"")
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


def test_a_canonical_turn_wakes_runs_and_settles(site, monkeypatch):
    """One dispatch produces a real wake, a real wall and a real settlement."""
    stub_pane(monkeypatch, site)
    notes = site / "notes.md"
    notes.write_text("Read the lifecycle source; the wake settled through the "
                     "canonical path.\n", encoding="utf-8")
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome",
                             notes_file=notes, timeout=30)
    supervisor = Supervisor(store=DispatchStore(site / "core-dispatch"),
                             registry=StaticRoleRegistry(["genome"]), worker=worker)
    record = supervisor.dispatch("genome", "trial-1", payload={
        "command": [sys.executable, "-c", "print('a useful reading')"],
        "self_pick_seconds": 0,
    })
    assert record.state.name == "completed", record.detail
    # The wall carries the settlement notes, and the tape records the yield.
    assert (site / "walls" / "genome.md").read_text(encoding="utf-8") == notes.read_text(encoding="utf-8")
    from mishe_tauftauf import seed
    _, pending, last_yield, _, _, _, _, _ = seed._state(site, "genome")
    assert pending is None
    assert last_yield is not None


def test_a_turn_that_owes_nothing_reports_skipped_and_claims_no_wake(site, monkeypatch):
    """A role with no owed turn produces no wake and no settlement."""
    from mishe_tauftauf import seed
    stub_pane(monkeypatch, site)
    # The tick refuses to create a wake: the wall is stable and no inbox
    # message is addressed to the role.
    monkeypatch.setattr(seed, "_state", lambda *a, **k: (None, None, None, None, None, None, None, None))
    notes = site / "notes.md"
    notes.write_text("Nothing was due.\n", encoding="utf-8")
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome",
                             notes_file=notes, timeout=30)
    result = worker.run(Dispatch(role="genome", key="trial-1", payload={
        "command": [sys.executable, "-c", "print('nothing due')"]}))
    assert result.outcome == "skipped"
    assert not (site / "walls" / "genome.md").exists()


def test_a_turn_whose_command_fails_leaves_the_wake_pending(site, monkeypatch):
    """A failed command settles nothing: the wake stays owed."""
    stub_pane(monkeypatch, site)
    notes = site / "notes.md"
    notes.write_text("The command failed; the wake is still pending.\n", encoding="utf-8")
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome",
                             notes_file=notes, timeout=30)
    result = worker.run(Dispatch(role="genome", key="trial-1", payload={
        "command": [sys.executable, "-c", "raise SystemExit(4)"]}))
    assert result.outcome == "failed"
    from mishe_tauftauf import seed
    pending = seed._state(site, "genome")[1]
    assert pending is not None, "a failed turn must leave its wake owed"
    assert not (site / "walls" / "genome.md").exists()


def test_the_observation_reads_the_canonical_state(site, monkeypatch):
    stub_pane(monkeypatch, site)
    observation = CanonicalObservation(home=site, role="genome")
    reading = observation()
    assert reading.fresh is True
    assert "pending=none" in reading.detail


def test_a_stale_observation_makes_the_turn_unknown_and_creates_no_wake(site, monkeypatch):
    """A stale dashboard must not read as a pass, and must not create a wake."""
    import json
    from datetime import datetime, timezone
    stub_pane(monkeypatch, site)
    stale = datetime.fromtimestamp(0, timezone.utc).isoformat()
    (site / "dashboards" / "genome.json").write_text(json.dumps({
        "version": 1, "slug": "genome", "home": str(site.resolve()),
        "created": stale, "frame": "STATE: GREEN\n", "ok": True,
        "sha256": "0" * 64,
    }), encoding="utf-8")
    notes = site / "notes.md"
    notes.write_text("The observation was stale, so the turn is unknown.\n", encoding="utf-8")
    worker = CanonicalWorker(home=site, session="mishe-trial", role="genome",
                             notes_file=notes, timeout=30)
    result = worker.run(Dispatch(role="genome", key="trial-1", payload={
        "command": [sys.executable, "-c", "print('must not run')"]}))
    assert result.outcome == "unknown"
    from mishe_tauftauf import seed
    assert seed._state(site, "genome")[1] is None, "a stale observation created a wake"
    assert not (site / "walls" / "genome.md").exists()


def test_the_observation_reports_an_unreadable_state_as_unknown(site, monkeypatch):
    from mishe_tauftauf import seed
    monkeypatch.setattr(seed, "_state", lambda *a, **k: (_ for _ in ()).throw(OSError("tape unreadable")))
    reading = CanonicalObservation(home=site, role="genome")()
    assert reading.fresh is False
    assert reading.healthy is False
