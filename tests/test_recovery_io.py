import fcntl
import json
import os
from pathlib import Path
import sys
import signal
import subprocess
import time

import pytest

from mishe_tauftauf import recovery_io as recovery


def config():
    return {"interval": 10, "stable_seconds": 20, "deadline": 60,
            "render_slots": 1, "render_cooldown": 30, "probes": []}


def sample():
    return {"memory_full": 40., "io_full": 60., "available_ratio": .04}


def test_controller_runs_with_shared_seed_and_publication_locks_held(tmp_path):
    for name in (".seed.lock", ".publication.lock"):
        (tmp_path / name).touch()
    with (tmp_path / ".seed.lock").open("r+") as seed, (tmp_path / ".publication.lock").open("r+") as pub:
        fcntl.flock(seed, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(pub, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        row = recovery.check(tmp_path, config(), observation=sample(), now=100)
        assert time.monotonic() - started < 2
    assert row["capacity"]["limited"]
    assert row["functions"]["seed-lock"]["state"] == "FAILED"
    assert list((tmp_path / "recovery/events").glob("*.json"))
    assert list((tmp_path / "recovery/outbox").glob("*.json"))


def test_identical_failure_does_not_generate_more_notifications(tmp_path):
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    before = set((tmp_path / "recovery/outbox").glob("*.json"))
    recovery.check(tmp_path, config(), observation=sample(), now=110)
    assert set((tmp_path / "recovery/outbox").glob("*.json")) == before


def test_failed_and_timed_out_probes_keep_original_output():
    row = recovery.run_probe([sys.executable, "-c", "print('original failure'); raise SystemExit(7)"], timeout=2)
    assert row["ok"] is False
    assert row["code"] == 7
    assert "original failure" in row["stdout"]
    started = time.monotonic()
    row = recovery.run_probe([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)"], timeout=.1)
    assert time.monotonic() - started < 3
    assert row["ok"] is None
    assert "started" in row["stdout"]


def test_corrupt_state_is_retained_and_cannot_be_reported_healthy(tmp_path):
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    state = tmp_path / "recovery/state.json"
    state.write_text("{original torn state")
    with pytest.raises(ValueError):
        recovery.check(tmp_path, config(), observation=sample(), now=110)
    assert state.read_text() == "{original torn state"
    assert recovery.status(tmp_path, now=110)["state"] == "UNKNOWN"


def test_missing_stale_and_future_controller_never_read_green(tmp_path):
    assert recovery.status(tmp_path, now=100)["state"] == "UNKNOWN"
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    assert recovery.status(tmp_path, now=200)["state"] == "UNKNOWN"
    assert recovery.status(tmp_path, now=99)["state"] == "UNKNOWN"


def test_backup_requires_explicit_matching_acceptance(tmp_path):
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    row = recovery.check(tmp_path, config(), observation=sample(), now=161)
    assert row["functions"]["host-pressure"]["backup_requested"] == "genome"
    with pytest.raises(ValueError):
        recovery.accept_incident(tmp_path, "host-pressure", owner="witness", now=162)
    row = recovery.accept_incident(tmp_path, "host-pressure", owner="genome", now=162)
    assert row["functions"]["host-pressure"]["resolver"] == "genome"


def test_new_probe_does_not_close_from_an_unrelated_success(tmp_path):
    (tmp_path / ".seed.lock").touch()
    cfg = config()
    cfg["probes"] = [{"name": "provider", "owner": "health", "backup": "genome",
                      "argv": [sys.executable, "-c", "raise SystemExit(2)"], "timeout": 2}]
    row = recovery.check(tmp_path, cfg, observation=sample(), now=100)
    assert row["functions"]["provider"]["state"] == "FAILED"
    assert row["functions"]["seed-lock"]["state"] == "HEALTHY"


def test_guard_does_not_accept_three_field_or_invalid_function_state(tmp_path):
    root = tmp_path / "recovery"
    root.mkdir()
    (root / "state.json").write_text(json.dumps({"version": 1, "checked_at": 100, "state": "GREEN"}))
    assert recovery.status(tmp_path, now=100)["state"] == "UNKNOWN"


def test_admission_returns_explicit_unknown_on_defer_and_restores_original_command(tmp_path):
    from mishe_tauftauf import render_admission as admission
    cfg = config()
    recovery.check(tmp_path, cfg, observation=sample(), now=100)
    command = [sys.executable, "-c", "print('original renderer timestamp=99')"]
    first = admission.render(tmp_path, "health", command, now=100)
    assert first["code"] == 0
    second = admission.render(tmp_path, "health", command, now=101)
    assert second["code"] == 1
    assert second["stdout"].startswith("UNKNOWN")
    assert "timestamp=99" in second["stdout"]
    assert "cached_at=100" in second["stdout"]
    third = admission.render(tmp_path, "health", command, now=131)
    assert not third["stdout"].startswith("UNKNOWN")


def test_occupied_slot_is_nonblocking_and_cannot_fake_a_render(tmp_path):
    from mishe_tauftauf import render_admission as admission
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    slot = tmp_path / "recovery/render-slot-0.lock"
    with slot.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        row = admission.render(tmp_path, "health", [sys.executable, "-c", "print('should not execute')"], now=100)
    assert row["stdout"].startswith("UNKNOWN")
    assert "should not execute" not in row["stdout"]


def test_large_gap_between_good_probes_cannot_prove_continuous_restoration(tmp_path):
    (tmp_path / ".seed.lock").touch()
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    clear = {"memory_full": 0., "io_full": 0., "available_ratio": .5}
    recovery.check(tmp_path, config(), observation=clear, now=110)
    row = recovery.check(tmp_path, config(), observation=clear, now=1000)
    assert row["capacity"]["limited"] is True


def test_renderer_retains_original_python_import_root(tmp_path, monkeypatch):
    from mishe_tauftauf import render_admission as admission
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    monkeypatch.setenv("PYTHONPATH", "/new/recovery/snapshot")
    monkeypatch.setenv("MISHE_RECOVERY_PREVIOUS_PYTHONPATH_SET", "x")
    monkeypatch.setenv("MISHE_RECOVERY_PREVIOUS_PYTHONPATH", "/original/runtime")
    row = admission.render(tmp_path, "health", [sys.executable, "-c", "import os; print(os.environ['PYTHONPATH'])"], now=100)
    assert row["stdout"].strip() == "/original/runtime"


def test_tampered_green_aggregate_does_not_hide_failed_function(tmp_path):
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    path = tmp_path / "recovery/state.json"
    row = json.loads(path.read_text())
    row["state"] = "GREEN"
    path.write_text(json.dumps(row))
    assert recovery.status(tmp_path, now=100)["state"] == "UNKNOWN"


@pytest.mark.parametrize("value", [[], None, 7, {"version": 1}])
def test_nonobject_or_incomplete_state_is_unknown(tmp_path, value):
    root = tmp_path / "recovery"
    root.mkdir()
    (root / "state.json").write_text(json.dumps(value))
    assert recovery.status(tmp_path, now=100)["state"] == "UNKNOWN"


def test_noisy_probe_has_a_production_bound_and_keeps_prefix():
    row = recovery.run_probe([sys.executable, "-c", "import os;\nwhile True: os.write(1,b'x'*8192)"], timeout=2, output_limit=16384)
    assert row["ok"] is None
    assert len(row["stdout"]) <= 16384
    assert "output limit" in row["stderr"]


def test_removing_a_probe_cannot_hide_its_open_failure(tmp_path):
    cfg = config()
    cfg["probes"] = [{"name": "provider", "owner": "health", "backup": "genome",
                      "argv": [sys.executable, "-c", "raise SystemExit(2)"], "timeout": 2}]
    recovery.check(tmp_path, cfg, observation=sample(), now=100)
    with pytest.raises(ValueError, match="coverage removed"):
        recovery.check(tmp_path, config(), observation=sample(), now=110)


def test_one_bad_notification_does_not_starve_later_events(tmp_path, monkeypatch):
    root = tmp_path / "recovery"
    recovery.event(root, {"first": "failure"}, recipients=["health"])
    recovery.event(root, {"second": "failure"}, recipients=["genome"])
    called = []
    def fail(argv, **kwargs):
        called.append(argv[-1])
        return {"ok": None, "code": None, "stdout": "", "stderr": "controlled timeout"}
    monkeypatch.setattr(recovery, "run_probe", fail)
    recovery.deliver(tmp_path)
    recovery.deliver(tmp_path)
    assert len(called) == 2 and called[0] != called[1]


def test_evaluation_cannot_claim_success_with_missing_current_monitoring(tmp_path):
    root = tmp_path / "recovery"
    root.mkdir()
    recovery.save(root / "baseline.json", {"activated_at": 100, "samples": [{"memory_full": 40, "io_full": 60, "control_ok": False}]})
    (root / "samples.jsonl").write_text(json.dumps({"at": 110, "memory_full": 1, "io_full": 1, "control_ok": True}) + "\n")
    assert recovery.evaluate(tmp_path, now=200)["verdict"] == "UNKNOWN"


def test_original_renderer_is_bounded_when_its_wrapper_is_killed(tmp_path):
    pid_path = tmp_path / "renderer.pid"
    code = "import os,time; from pathlib import Path; Path(" + repr(str(pid_path)) + ").write_text(str(os.getpid())); time.sleep(60)"
    wrapper = "from mishe_tauftauf.recovery_io import run_probe; import sys; run_probe([sys.executable,'-c',sys.argv[1]],timeout=.5)"
    parent = subprocess.Popen([sys.executable, "-c", wrapper, code], start_new_session=True)
    try:
        cutoff = time.monotonic() + 3
        while not pid_path.exists() and time.monotonic() < cutoff:
            time.sleep(.01)
        assert pid_path.exists()
        child = int(pid_path.read_text())
        parent.kill()
        parent.wait(timeout=2)
        cutoff = time.monotonic() + 3
        while Path(f"/proc/{child}/stat").exists() and time.monotonic() < cutoff:
            if Path(f"/proc/{child}/stat").read_text().split()[2] == "Z":
                break
            time.sleep(.02)
        assert not Path(f"/proc/{child}/stat").exists() or Path(f"/proc/{child}/stat").read_text().split()[2] == "Z"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=2)


def test_real_outbox_publication_uses_prose_and_is_idempotent(tmp_path):
    from mishe_tauftauf.feed import Feed
    root = tmp_path / "recovery"
    recovery.event(root, {"at": 100, "function": {"name": "seed-lock", "state": "FAILED", "episode": 1, "resolver": "health"}}, recipients=["health"])
    recovery.deliver(tmp_path)
    assert not list((root / "outbox").glob("*.json"))
    recovery.deliver(tmp_path)
    entries = Feed(tmp_path).entries()
    assert len(entries) == 1
    assert entries[0].body.startswith("[dm] to=health\n")
    assert "seed-lock" in entries[0].body


def test_transient_pass_during_open_failure_does_not_create_a_notice_flood(tmp_path, monkeypatch):
    observed = {"ok": False, "seconds": .01}
    monkeypatch.setattr(recovery, "seed_lock_probe", lambda home: dict(observed))
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    initial = set((tmp_path / "recovery/outbox").glob("*.json"))
    observed["ok"] = True
    recovery.check(tmp_path, config(), observation=sample(), now=110)
    observed["ok"] = False
    recovery.check(tmp_path, config(), observation=sample(), now=120)
    assert set((tmp_path / "recovery/outbox").glob("*.json")) == initial


def test_renderer_slot_survives_wrapper_death_until_helper_exits(tmp_path):
    from mishe_tauftauf import render_admission as admission
    recovery.check(tmp_path, config(), observation=sample(), now=100)
    marker = tmp_path / "helper.pid"
    child = "import os,time;from pathlib import Path;Path(" + repr(str(marker)) + ").write_text(str(os.getpid()));time.sleep(60)"
    code = "from pathlib import Path;import sys;from mishe_tauftauf import render_admission as a;original=a.run_probe;a.run_probe=lambda argv,**kw:original(argv,timeout=1.5,**({'pass_fds':kw['pass_fds']} if 'pass_fds' in kw else {}));a.render(Path(sys.argv[1]),'health',[sys.executable,'-c',sys.argv[2]],now=100)"
    wrapper = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), child])
    try:
        cutoff = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < cutoff:
            time.sleep(.01)
        assert marker.exists()
        wrapper.kill()
        wrapper.wait(timeout=2)
        row = admission.render(tmp_path, "genome", [sys.executable, "-c", "print('overlap')"], now=100)
        assert row["code"] != 0 and "overlap" not in row["stdout"]
        time.sleep(1.7)
        row = admission.render(tmp_path, "genome", [sys.executable, "-c", "print('recovered')"], now=100)
        assert row["code"] == 0 and "recovered" in row["stdout"]
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=2)


def test_wrapper_death_cannot_leave_a_detached_pipe_descendant(tmp_path):
    marker = tmp_path / "grandchild.pid"
    grandchild = "import os,time;from pathlib import Path;Path(" + repr(str(marker)) + ").write_text(str(os.getpid()));time.sleep(60)"
    child = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',sys.argv[1]]);time.sleep(.4)"
    wrapper_code = "import sys;from mishe_tauftauf.recovery_io import run_probe;run_probe([sys.executable,'-c',sys.argv[1],sys.argv[2]],timeout=2)"
    wrapper = subprocess.Popen([sys.executable, "-c", wrapper_code, child, grandchild])
    try:
        cutoff = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < cutoff:
            time.sleep(.01)
        assert marker.exists()
        pid = int(marker.read_text())
        wrapper.kill()
        wrapper.wait(timeout=2)
        time.sleep(.8)
        stat = Path(f"/proc/{pid}/stat")
        assert not stat.exists() or stat.read_text().split()[2] == "Z"
    finally:
        if wrapper.poll() is None:
            wrapper.kill();wrapper.wait(timeout=2)
        if marker.exists():
            try:os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:pass
