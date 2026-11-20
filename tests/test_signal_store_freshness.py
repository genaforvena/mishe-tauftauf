"""Exercise expiry through the installed-scheduler path with fixture signals."""

import pytest

from mishe_tauftauf import arm, runtime_cli
from mishe_tauftauf import signal_store as store
from mishe_tauftauf.state_files import read_json


def _run_due(home, monkeypatch):
    home.mkdir()
    (home / "units-installed").touch()
    (home / "hold").touch()  # Keep the fixture from resuming real roles.
    for name, observed_at in (
        ("equal", 1000.0),
        ("expired", 999.875),
        ("future", 1031.0),
        ("missing-stamp", 1020.0),
        ("corrupt", 1020.0),
        ("never", None),
    ):
        store.register(home, name, max_age=30.0, now=1000.0)
        if observed_at is not None:
            store.observe(home, name, "GREEN", now=observed_at, source="fixture")
    signals = home / "signals"
    (signals / "missing-stamp.seen").unlink()
    (signals / "corrupt.json").write_bytes(b'{"state":')

    timers = []
    monkeypatch.setattr(runtime_cli.shutil, "which", lambda name: "/fixture/systemd-run")
    monkeypatch.setattr(runtime_cli.time, "time", lambda: 1030.0)
    monkeypatch.setattr(arm, "systemd_scheduler", lambda epoch, **kwargs: timers.append(epoch))
    scheduler = runtime_cli.scheduler(home)
    assert arm.due(home, now=1030.0, scheduler=scheduler) == ["expired", "never"]
    assert read_json(home / "next-due.json") == {"at": 1030.0}
    assert store.read(home, "equal", now=1030.0).state == store.OK
    assert store.read(home, "future", now=1030.0).state == store.OK
    for name in ("expired", "never", "missing-stamp", "corrupt"):
        assert store.read(home, name, now=1030.0).state == store.UNKNOWN
    assert (signals / "corrupt.json").read_bytes() == b'{"state":'

    monkeypatch.setattr(runtime_cli.time, "time", lambda: 1031.0)
    assert arm.due(home, now=1031.0, scheduler=scheduler) == ["equal"]
    assert arm.due(home, now=1031.0, scheduler=scheduler) == []
    assert timers == [1030.0, 1061.0, 1061.0]
    assert read_json(home / "next-due.json") == {"at": 1061.0}
    return {p.relative_to(home): p.read_bytes() for p in (*signals.iterdir(), home / "next-due.json")}


@pytest.mark.parametrize("verifier", ["missing", "broken"])
def test_due_preserves_edge_and_timer_bytes_without_a_working_verifier(tmp_path, monkeypatch, verifier):
    with monkeypatch.context() as env:
        for key in ("MISHE_SEED_HOME", "MISHE_TAUFTAUF_HOME", "MISHE_SIGNAL_FRESHNESS_BIN"):
            env.delenv(key, raising=False)
        expected = _run_due(tmp_path / "reference", env)
    home = tmp_path / "site"
    monkeypatch.setenv("MISHE_SEED_HOME", str(home))
    monkeypatch.delenv("MISHE_SIGNAL_FRESHNESS_BIN", raising=False)
    if verifier == "broken":
        executable = tmp_path / "broken"
        executable.write_text("#!/bin/sh\nexit 2\n")
        executable.chmod(0o755)
        monkeypatch.setenv("MISHE_SIGNAL_FRESHNESS_BIN", str(executable))
    assert _run_due(home, monkeypatch) == expected
