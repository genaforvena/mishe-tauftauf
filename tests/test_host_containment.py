"""The host safety boundary must fail closed independently of plant history."""
import threading
import time
import sys
import os
import pathlib

import pytest

from mishe_tauftauf import host_containment as guard


def evidence_naming(state, path, verification=("controlled verification",)):
    """Write structured restore evidence that names the exact latches present."""
    import json as _json
    latches = guard.latches_present(state)
    path.write_text(_json.dumps(dict(clears=latches, verification=list(verification))))
    return path


def sample(**changes):
    row = dict(oom_kill=0, memory=100, maximum=1000, tasks=1, task_max=100,
               available=8 * guard.GIB, disk_free=30 * guard.GIB,
               memory_pressure=0, io_pressure=0, limits_ok=True, ssh_available=True, host_io_available=True)
    row.update(changes)
    return row


def test_oom_trips_immediately_and_counter_reset_is_not_an_oom():
    assert guard.reason(sample(oom_kill=1), sample(), None, 10)[0] == "cgroup OOM"
    assert guard.reason(sample(), sample(oom_kill=1), None, 10)[0] is None


def test_sustained_pressure_trips_but_a_clear_sample_breaks_interval():
    bad = sample(memory_pressure=20, available=3 * guard.GIB)
    assert guard.reason(bad, sample(), None, 10) == (None, 10)
    assert guard.reason(bad, bad, 10, 20)[0] == "sustained resource pressure"
    assert guard.reason(sample(), bad, 10, 19) == (None, None)


def test_missing_observation_and_changed_limit_trip_immediately():
    assert guard.reason(None, sample(), None, 10)[0] == "resource observation unavailable"
    assert guard.reason(sample(limits_ok=False), sample(), None, 10)[0] == "resource limits changed"


def test_disk_and_host_headroom_have_independent_cutoffs():
    assert guard.reason(sample(disk_free=guard.GIB), sample(), None, 10)[0] == "root disk reserve low"
    assert guard.reason(sample(available=guard.GIB), sample(), 0, 10)[0] == "sustained resource pressure"


def test_admission_rejects_missing_stale_future_unknown_or_latched_evidence(tmp_path):
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    assert not guard.admitted(state, run, now=100)
    for record in ({"at": 70, "state": "READY"}, {"at": 101, "state": "READY"},
                   {"at": 100, "state": "UNKNOWN"}):
        guard.atomic(run / "status.json", record)
        assert not guard.admitted(state, run, now=100)
    guard.atomic(run / "status.json", {"at": 100, "state": "READY"})
    assert guard.admitted(state, run, now=100)
    (state / "tripped").touch()
    assert not guard.admitted(state, run, now=100)


def test_first_trip_evidence_survives_repeated_trip(tmp_path):
    guard.latch(tmp_path, "first", {"oom_kill": 1})
    original = (tmp_path / "tripped").read_bytes()
    guard.latch(tmp_path, "second", None)
    assert (tmp_path / "tripped").read_bytes() == original


def test_rearm_refuses_pressure_or_missing_observation():
    assert not guard.clear_to_rearm(None)
    assert not guard.clear_to_rearm(sample(memory=900))
    assert not guard.clear_to_rearm(sample(memory_pressure=3))
    assert guard.clear_to_rearm(sample())


@pytest.mark.parametrize("old_state", ["READY", "UNKNOWN"])
def test_restart_always_latches_even_with_readable_old_evidence(tmp_path, monkeypatch, old_state):
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    guard.atomic(run / "status.json", {"state": old_state, "at": 0, "pressured_since": 1})
    monkeypatch.setattr(guard, "STATE", state)
    monkeypatch.setattr(guard, "RUN", run)
    monkeypatch.setattr(guard, "observe", lambda config: sample())
    monkeypatch.setattr(guard, "stop", lambda config: [])
    monkeypatch.setattr(guard, "notify", lambda message: None)
    def end(_):
        raise InterruptedError
    monkeypatch.setattr(guard.time, "sleep", end)
    with pytest.raises(InterruptedError):
        guard.watch({})
    assert (state / "tripped").exists()
    assert guard.read(run / "status.json")["state"] == "TRIPPED"


def test_trip_waiting_during_rearm_is_retained_after_rearm(tmp_path, monkeypatch):
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    guard.latch(state, "old trip", None)
    guard.atomic(run / "status.json", {"state": "TRIPPED", "at": guard.time.time(), "observation": sample()})
    config = tmp_path / "config.json"
    guard.atomic(config, dict(user="test", uid=1000, cgroup=str(tmp_path / "cg"), units=[], limits={str(tmp_path / "cg"): {"memory.max": 1}}))
    for name, value in (("STATE", state), ("RUN", run), ("CONFIG", config)):
        monkeypatch.setattr(guard, name, value)
    monkeypatch.setattr(guard, "caller_inside_mishe", lambda *a, **kw: False)
    monkeypatch.setattr("sys.argv", ["guard", "rearm"])
    attempting, acquired = threading.Event(), threading.Event()
    def concurrent_trip():
        attempting.set()
        with guard.transition(state):
            acquired.set()
            guard.latch(state, "new trip", None)
    worker = threading.Thread(target=concurrent_trip)
    def observation(_, **kwargs):
        worker.start()
        assert attempting.wait(1)
        assert not acquired.wait(.05)
        return sample()
    monkeypatch.setattr(guard, "observe", observation)
    assert guard.main() == 0
    worker.join(2)
    assert not worker.is_alive()
    assert guard.read(state / "tripped")["reason"] == "new trip"


def test_stale_checker_stops_workload_even_with_a_wedged_transition_lock(tmp_path, monkeypatch):
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    config = tmp_path / "config.json"
    guard.atomic(config, dict(user="test", uid=1000, cgroup=str(tmp_path / "cg"), units=[], limits={str(tmp_path / "cg"): {"memory.max": 1}}))
    for name, value in (("STATE", state), ("RUN", run), ("CONFIG", config)):
        monkeypatch.setattr(guard, name, value)
    monkeypatch.setattr("sys.argv", ["guard", "check"])
    stopped = []
    monkeypatch.setattr(guard, "stop", lambda config: stopped.append(True))
    with guard.transition(state):
        assert guard.main() == 1
    assert stopped == [True, True]
    assert (state / "emergency").exists()
    guard.atomic(run / "status.json", {"state": "READY", "at": guard.time.time()})
    assert not guard.admitted(state, run)
    monkeypatch.setattr(guard, "caller_inside_mishe", lambda *a, **kw: False)
    monkeypatch.setattr("sys.argv", ["guard", "rearm"])
    monkeypatch.setattr(guard, "observe", lambda config: sample())
    with pytest.raises(SystemExit, match="emergency latch"):
        guard.main()


def test_failed_trip_persistence_still_stops_workload(tmp_path, monkeypatch):
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    config = tmp_path / "config.json"
    guard.atomic(config, dict(user="test", uid=1000, cgroup=str(tmp_path / "cg"), units=[], limits={str(tmp_path / "cg"): {"memory.max": 1}}))
    for name, value in (("STATE", state), ("RUN", run), ("CONFIG", config)):
        monkeypatch.setattr(guard, name, value)
    monkeypatch.setattr("sys.argv", ["guard", "check"])
    stopped = []
    monkeypatch.setattr(guard, "stop", lambda config: stopped.append(True))
    def readonly(*args):
        raise OSError("read-only state filesystem")
    monkeypatch.setattr(guard, "latch", readonly)
    assert guard.main() == 1
    assert stopped == [True, True]
    stopped.clear()
    with pytest.raises(OSError, match="read-only"):
        guard.watch({})
    assert stopped == [True]


def test_emergency_persistence_failure_still_stops_workload(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guard, "STATE", tmp_path / "nonexistent")
    stopped = []
    monkeypatch.setattr(guard, "stop", lambda config: stopped.append(True))
    guard.emergency({}, "test")
    assert stopped == [True]
    assert "persistence failed" in capsys.readouterr().err


@pytest.mark.parametrize("content", [None, "{}", "not json"])
def test_broken_configuration_uses_independent_root_unit_target(tmp_path, monkeypatch, content):
    config = tmp_path / "config.json"
    if content is not None:
        config.write_text(content)
    monkeypatch.setattr(guard, "CONFIG", config)
    monkeypatch.setattr(guard, "STATE", tmp_path)
    monkeypatch.setattr("sys.argv", ["guard", "--cgroup", "/test/emergency-target", "check"])
    stopped = []
    monkeypatch.setattr(guard, "stop", lambda config: stopped.append(config["cgroup"]))
    assert guard.main() == 1
    assert stopped == ["/test/emergency-target"]


def test_missing_status_is_unknown_not_a_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guard, "RUN", tmp_path)
    monkeypatch.setattr("sys.argv", ["guard", "status"])
    assert guard.main() == 1
    assert '"UNKNOWN"' in capsys.readouterr().out


@pytest.mark.parametrize("field,value", [("units", None), ("limits", None), ("units", [1]), ("limits", {"/cg": None}), ("stop_cgroups", None)])
def test_malformed_configuration_fields_use_independent_target(tmp_path, monkeypatch, field, value):
    config = tmp_path / "config.json"
    row = dict(user="mesh-home", uid=1000, cgroup="/test/target", units=[], limits={"/cg": {"memory.max": 1}})
    row[field] = value
    guard.atomic(config, row)
    monkeypatch.setattr(guard, "CONFIG", config)
    monkeypatch.setattr(guard, "STATE", tmp_path)
    monkeypatch.setattr("sys.argv", ["guard", "--cgroup", "/test/target", "check"])
    stopped = []
    monkeypatch.setattr(guard, "stop", lambda config: stopped.append(config["cgroup"]))
    assert guard.main() == 1
    assert stopped == ["/test/target"]


def test_kernel_stop_does_not_depend_on_well_formed_inventory(tmp_path):
    kill = tmp_path / "cgroup.kill"
    kill.touch()
    receipts = guard.stop(dict(cgroup=str(tmp_path), units=None))
    assert kill.read_text() == "1"
    assert receipts[0] == {"kill": "requested"}


def test_throttled_workload_pressure_is_retained_without_claiming_ssh_failure():
    row = sample(memory_pressure=90, io_pressure=90)
    assert guard.reason(row, sample(), None, 10) == (None, None)
    assert row["memory_pressure"] == row["io_pressure"] == 90


def test_ssh_failure_is_an_independent_immediate_stop():
    assert guard.reason(sample(ssh_available=False), sample(), None, 10)[0] == "SSH listener unavailable"
    assert not guard.clear_to_rearm(sample(ssh_available=False))


def test_host_filesystem_probe_writes_reads_and_syncs(tmp_path):
    assert guard.filesystem_probe(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_bounded_functional_probe_kills_a_stalled_process():
    started = time.monotonic()
    receipt = {}
    assert guard.bounded_command([sys.executable, "-c", "import time;time.sleep(60)"], timeout=.05, receipt=receipt) is None
    assert time.monotonic() - started < 1
    assert receipt["timed_out"] and receipt["seconds"] < 1


def test_failed_probe_preserves_exit_status_and_stderr():
    receipt = {}
    assert guard.bounded_command([sys.executable, "-c", "import sys;print('cause',file=sys.stderr);sys.exit(7)"], receipt=receipt)[0] == 7
    assert receipt["code"] == 7 and "cause" in receipt["stderr"]


def test_host_io_failure_is_independent_of_listener_and_headroom():
    assert guard.reason(sample(host_io_available=False), sample(), None, 10)[0] == "host filesystem probe unavailable"
    assert not guard.clear_to_rearm(sample(host_io_available=False))


def test_pressure_warning_transition_is_durable_and_names_bounded_resolver(tmp_path):
    row = sample(io_pressure=60)
    guard.record_pressure(tmp_path, row, True)
    files = list(tmp_path.glob("pressure-*.json")); assert len(files) == 1
    original = files[0].read_bytes()
    guard.record_pressure(tmp_path, sample(), False)
    assert files[0].read_bytes() == original
    assert len(list(tmp_path.glob("pressure-*.json"))) == 2
    event = guard.read(files[0])
    assert event["resolver"] == "host-sentinel"
    assert event["next_probe_seconds"] <= 10
    assert event["observation"]["io_pressure"] == 60


def test_one_second_filesystem_warning_is_preserved_under_longer_deadline(tmp_path, monkeypatch):
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    row = sample(probes={"io": {"seconds": 1.2, "timed_out": False}})
    monkeypatch.setattr(guard, "observe", lambda config: row)
    monkeypatch.setattr(guard, "stop", lambda config: [])
    monkeypatch.setattr(guard, "notify", lambda message: None)
    def end(_): raise InterruptedError
    monkeypatch.setattr(guard.time, "sleep", end)
    with pytest.raises(InterruptedError): guard.watch({})
    assert guard.read(run / "status.json")["pressure_warning"]
    events = list(state.glob("pressure-*.json")); assert len(events) == 1
    assert guard.read(events[0])["observation"]["probes"]["io"]["seconds"] == 1.2


def test_normal_shutdown_keeps_life_support_cgroup_and_units(tmp_path, monkeypatch):
    normal, repair, life = [tmp_path / name for name in ("normal", "repair", "life")]
    for path in (normal, repair, life):
        path.mkdir(); (path / "cgroup.kill").touch()
    commands = []
    monkeypatch.setattr(guard, "user_command", lambda config, *args: commands.append(args) or type("R", (), dict(returncode=0, stderr=""))())
    guard.stop(dict(cgroup=str(tmp_path), stop_cgroups=[str(normal), str(repair)], units=["normal.service"]))
    assert (normal / "cgroup.kill").read_text() == "1"
    assert (repair / "cgroup.kill").read_text() == "1"
    assert (life / "cgroup.kill").read_text() == ""
    assert all("health.service" not in args for args in commands)


def test_life_support_requires_exact_kernel_budget_even_when_normal_is_latched(tmp_path):
    life = tmp_path / "life"; life.mkdir()
    limits = {"memory.max": 3 * guard.GIB, "pids.max": 256, "cpu.max": "100000 100000"}
    for name, value in limits.items(): (life / name).write_text(str(value))
    config = dict(life_support=dict(cgroup=str(life)), limits={str(life): limits})
    assert guard.lifeline_safe(config)
    (life / "memory.max").write_text("max")
    assert not guard.lifeline_safe(config)

def test_restore_refuses_unstable_or_missing_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "STATE", tmp_path)
    monkeypatch.setattr(guard, "RUN", tmp_path)
    guard.atomic(tmp_path / "status.json", dict(at=guard.time.time(), state="TRIPPED", observation=sample(), clear_since=guard.time.time()))
    with pytest.raises(SystemExit): guard.restore({}, None)


def test_mode_is_unknown_when_observation_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guard, "RUN", tmp_path)
    monkeypatch.setattr("sys.argv", ["guard", "mode"])
    assert guard.main() == 1
    assert capsys.readouterr().out.strip() == "UNKNOWN"


def test_rearm_uses_fresh_functional_result_and_only_refreshes_cheap_resources(tmp_path, monkeypatch):
    state, run = tmp_path / "state", tmp_path / "run"; state.mkdir(); run.mkdir()
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    guard.atomic(run / "status.json", dict(at=guard.time.time(), state="TRIPPED", observation=sample()))
    guard.latch(state, "old", None)
    calls = []
    def cheap(config, **kwargs):
        calls.append(kwargs)
        return sample()
    monkeypatch.setattr(guard, "observe", cheap)
    guard.rearm({})
    assert calls == [{"functional": False}]
    assert not (state / "tripped").exists()


def test_failed_staged_restore_returns_to_life_support_and_retains_step_evidence(tmp_path, monkeypatch):
    state, run, evidence = tmp_path / "state", tmp_path / "run", tmp_path / "artifacts"
    state.mkdir(); run.mkdir(); evidence.mkdir()
    guard.latch(state, "original failure", None)
    diagnosis = evidence_naming(state, evidence / "diagnosis.json")
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    guard.atomic(run / "status.json", dict(at=guard.time.time(), state="READY"))
    monkeypatch.setattr(guard, "rearm", lambda config, **kwargs: (state / "tripped").unlink())
    monkeypatch.setattr(guard.time, "sleep", lambda duration: None)
    stopped = []
    monkeypatch.setattr(guard, "stop", lambda config: stopped.append(config["stop_cgroups"]))
    def start(config, *args):
        guard.latch(state, "controlled failure during first stage", None)
        return type("R", (), dict(returncode=0, stderr=""))()
    monkeypatch.setattr(guard, "user_command", start)
    config = dict(evidence_root=str(evidence), life_support=dict(units=["health.service"]),
                  resume_units=["one.service", "two.service"], stop_cgroups=["normal", "repair"])
    with pytest.raises(RuntimeError, match="closed"):
        guard.restore(config, diagnosis)
    assert stopped == [["normal", "repair"]]
    assert guard.read(state / "tripped")["reason"] == "controlled failure during first stage"
    assert len(list(state.glob("restore-*-intent.json"))) == 1
    assert not (state / "restoring").exists()


def test_interrupted_restore_is_detected_without_discarding_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "STATE", tmp_path)
    guard.atomic(tmp_path / "restoring", dict(at=10, id="original"))
    assert guard.restoration_expired(now=31)
    assert guard.read(tmp_path / "restoring")["id"] == "original"


def test_failed_restoration_intent_write_never_opens_admission(tmp_path, monkeypatch):
    state, run = tmp_path / "state", tmp_path / "run"; state.mkdir(); run.mkdir()
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    guard.atomic(run / "status.json", dict(at=guard.time.time(), state="TRIPPED", observation=sample(),
                 clear_since=guard.time.monotonic()-121, life_support=dict(state="PANES_LIVE")))
    guard.latch(state, "original failure", None)
    monkeypatch.setattr(guard, "observe", lambda config, **kwargs: sample())
    real_atomic = guard.atomic
    def fail(path, value):
        if path.name == "restoring": raise OSError("controlled failed intent write")
        real_atomic(path, value)
    monkeypatch.setattr(guard, "atomic", fail)
    with pytest.raises(OSError): guard.rearm({}, restoration=True, request=dict(at=guard.time.time()), expected=guard.latches_present(state))
    assert guard.read(state / "tripped")["reason"] == "original failure"
    assert not guard.admitted(state, run)


def test_life_observation_requires_both_roles_and_their_actual_cgroup(tmp_path, monkeypatch):
    group = next(line[3:] for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
    lines = "".join(f"{role}\t{index}\t0\t{os.getpid()}\t{tmp_path}\tomp\n" for role in ("health", "genome") for index in (0, 1))
    monkeypatch.setattr(guard, "bounded_command", lambda *args, **kwargs: (0, lines.encode()))
    config = dict(user="test", life_support=dict(socket="unused", session="life", home=str(tmp_path), cgroup=group, roles=["health", "genome"]))
    assert guard.life_observation(config)["state"] == "PANES_LIVE"
    config["life_support"]["cgroup"] = "/not-this-process"
    assert guard.life_observation(config)["state"] == "RED"


def test_a_shell_placeholder_is_not_a_live_mind(tmp_path, monkeypatch):
    """The protected session must not read as healthy when no mind runs.

    A pane running a shell renders nothing and answers nothing.  Reporting
    it as live made the session look healthy while it held only
    placeholders, which is the false reading this observation must not give.
    """
    group = next(line[3:] for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
    lines = "".join(f"{role}\t{index}\t0\t{os.getpid()}\t{tmp_path}\tsh\n" for role in ("health", "genome") for index in (0, 1))
    monkeypatch.setattr(guard, "bounded_command", lambda *args, **kwargs: (0, lines.encode()))
    config = dict(user="test", life_support=dict(socket="unused", session="life", home=str(tmp_path), cgroup=group, roles=["health", "genome"]))
    row = guard.life_observation(config)
    assert row["state"] == "RED"
    assert row["minds_running"] is False


def test_a_live_mind_with_a_shell_renderer_is_still_live(tmp_path, monkeypatch):
    """The renderer pane may be a shell between frames; the mind may not."""
    group = next(line[3:] for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
    rows = []
    for role in ("health", "genome"):
        rows.append(f"{role}\t0\t0\t{os.getpid()}\t{tmp_path}\tsh")
        rows.append(f"{role}\t1\t0\t{os.getpid()}\t{tmp_path}\tomp")
    monkeypatch.setattr(guard, "bounded_command", lambda *args, **kwargs: (0, ("\n".join(rows) + "\n").encode()))
    config = dict(user="test", life_support=dict(socket="unused", session="life", home=str(tmp_path), cgroup=group, roles=["health", "genome"]))
    row = guard.life_observation(config)
    assert row["state"] == "PANES_LIVE"
    assert row["minds_running"] is True


def test_a_contained_claude_mind_counts_as_an_engine(tmp_path, monkeypatch):
    """The site's claude-launched minds are minds, not absent panes."""
    group = next(line[3:] for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
    rows = []
    for role in ("health", "genome"):
        rows.append(f"{role}\t0\t0\t{os.getpid()}\t{tmp_path}\tpython3")
        rows.append(f"{role}\t1\t0\t{os.getpid()}\t{tmp_path}\tclaude")
    monkeypatch.setattr(guard, "bounded_command", lambda *args, **kwargs: (0, ("\n".join(rows) + "\n").encode()))
    config = dict(user="test", life_support=dict(socket="unused", session="life", home=str(tmp_path), cgroup=group, roles=["health", "genome"]))
    row = guard.life_observation(config)
    assert row["state"] == "PANES_LIVE"
    assert row["minds_running"] is True
    assert row["panes"]["health.1"]["engine"] is True


def test_an_uncontained_claude_pane_is_not_a_protected_mind(tmp_path, monkeypatch):
    """A claude process outside the protected cgroup must not satisfy minds_running."""
    rows = []
    for role in ("health", "genome"):
        rows.append(f"{role}\t0\t0\t{os.getpid()}\t{tmp_path}\tpython3")
        rows.append(f"{role}\t1\t0\t{os.getpid()}\t{tmp_path}\tclaude")
    monkeypatch.setattr(guard, "bounded_command", lambda *args, **kwargs: (0, ("\n".join(rows) + "\n").encode()))
    config = dict(user="test", life_support=dict(socket="unused", session="life", home=str(tmp_path), cgroup="/not-this-process", roles=["health", "genome"]))
    row = guard.life_observation(config)
    assert row["state"] == "RED"
    assert row["minds_running"] is False
    assert row["panes"]["genome.1"]["engine"] is False


def test_ssh_probe_tolerates_a_slow_successful_handshake(monkeypatch):
    seen = []
    def command(argv, **kwargs):
        seen.append((argv[0], kwargs.get("timeout", 1)))
        return (0, b" ssh-ed25519 AAAA\n") if "ssh-keyscan" in argv[0] else (0, b"")
    monkeypatch.setattr(guard, "bounded_command", command)
    ok, io_ok, _ = guard.functional_probes(dict(user="test"))
    assert ok and io_ok
    assert dict(seen)["/usr/bin/ssh-keyscan"] >= 5


def test_protected_session_observation_allows_a_loaded_tmux_reply(monkeypatch):
    seen = []
    monkeypatch.setattr(guard, "bounded_command", lambda argv, **kwargs: seen.append(kwargs.get("timeout")) or None)
    config = dict(user="test", life_support=dict(socket="unused", session="life", home="h", cgroup="/x", roles=["health"]))
    guard.life_observation(config)
    assert seen and min(seen) >= 2


def test_restore_refusal_leaves_existing_latch_and_workload_untouched(tmp_path, monkeypatch):
    state, run, evidence = tmp_path / "state", tmp_path / "run", tmp_path / "artifacts"
    state.mkdir(); run.mkdir(); evidence.mkdir()
    guard.latch(state, "original failure", None)
    diagnosis = evidence_naming(state, evidence / "diagnosis.json")
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    stopped = []
    monkeypatch.setattr(guard, "stop", lambda config: stopped.append(config))
    monkeypatch.setattr(guard, "observe", lambda config, **kwargs: sample())
    guard.atomic(run / "status.json", dict(at=guard.time.time(), state="TRIPPED", observation=sample(),
                 clear_since=guard.time.monotonic() - 10, life_support=dict(state="PANES_LIVE")))
    config = dict(evidence_root=str(evidence), life_support=dict(units=["health.service"]),
                  resume_units=["one.service"], stop_cgroups=["normal", "repair"])
    with pytest.raises(SystemExit):
        guard.restore(config, diagnosis)
    assert stopped == []
    assert guard.read(state / "tripped")["reason"] == "original failure"


def test_restoration_admits_only_the_unit_being_started(tmp_path):
    state, run = tmp_path / "state", tmp_path / "run"; state.mkdir(); run.mkdir()
    guard.atomic(run / "status.json", dict(at=100, state="READY"))
    guard.atomic(state / "restoring", dict(at=100, next_unit="one.service"))
    assert guard.admitted(state, run, now=100, unit="one.service")
    assert not guard.admitted(state, run, now=100, unit="dependency.service")
    assert not guard.admitted(state, run, now=100)
    guard.atomic(state / "restoring", dict(at=50, next_unit="one.service"))
    assert not guard.admitted(state, run, now=100, unit="one.service")


def test_bounded_probe_accepts_a_slow_successful_real_child():
    receipt = {}
    result = guard.bounded_command([sys.executable, "-c", "import time,sys;time.sleep(1.2);print('ok')"],
                                   timeout=5, receipt=receipt)
    assert result is not None and result[0] == 0 and b"ok" in result[1]
    assert receipt["timed_out"] is False


def test_life_session_configuration_timeout_is_bounded_under_load(monkeypatch):
    seen = {}
    def command(argv, **kwargs):
        seen["set-option" in argv] = kwargs.get("timeout")
        return (0, b"")
    monkeypatch.setattr(guard, "bounded_command", command)
    monkeypatch.setattr(guard, "life_observation", lambda config: dict(state="PANES_LIVE"))
    monkeypatch.setattr(guard, "lifeline_safe", lambda config: True)
    config = dict(user="test", life_support=dict(socket="unused", session="life", units=["health.service"]))
    guard.ensure_life_support(config)
    assert seen[True] >= 2


def test_restore_retries_transition_lock_contention_without_latching(tmp_path, monkeypatch):
    calls = []
    def contended(config, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("containment transition lock unavailable")
    monkeypatch.setattr(guard, "rearm", contended)
    monkeypatch.setattr(guard.time, "sleep", lambda duration: None)
    guard.rearm_when_lock_free({}, dict(at=0))
    assert len(calls) == 3


def test_restore_refuses_after_exhausting_lock_retries_without_latching(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "rearm", lambda config, **kwargs: (_ for _ in ()).throw(TimeoutError("busy")))
    monkeypatch.setattr(guard.time, "sleep", lambda duration: None)
    with pytest.raises(TimeoutError):
        guard.rearm_when_lock_free({}, dict(at=0), attempts=3)


def test_restore_evidence_must_name_exactly_the_latches_it_clears(tmp_path, monkeypatch):
    state = tmp_path / "state"; state.mkdir()
    monkeypatch.setattr(guard, "STATE", state)
    guard.latch(state, "host pressure", None)
    good = evidence_naming(state, tmp_path / "good.json")
    assert guard.parse_evidence(good.read_bytes(), state)["verification"]
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"clears": {"tripped": {"reason": "other", "at": 1}}, "verification": ["x"]}')
    with pytest.raises(SystemExit): guard.parse_evidence(wrong.read_bytes(), state)
    unverified = tmp_path / "unverified.json"
    unverified.write_text(good.read_text().replace('"verification": ["controlled verification"]', '"verification": []'))
    with pytest.raises(SystemExit): guard.parse_evidence(unverified.read_bytes(), state)
    prose = tmp_path / "prose.md"; prose.write_text("# notes")
    with pytest.raises(SystemExit): guard.parse_evidence(prose.read_bytes(), state)




def test_restore_refuses_when_no_latch_exists(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"clears": {}, "verification": ["x"]}')
    with pytest.raises(SystemExit): guard.parse_evidence(artifact.read_bytes(), state)


def _fresh_restore_state(tmp_path, monkeypatch):
    state, run, evidence = tmp_path / "state", tmp_path / "run", tmp_path / "artifacts"
    state.mkdir(); run.mkdir(); evidence.mkdir()
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    guard.atomic(run / "status.json", dict(at=guard.time.time(), state="TRIPPED", observation=sample(),
                 clear_since=guard.time.monotonic() - 121, life_support=dict(state="PANES_LIVE")))
    monkeypatch.setattr(guard, "observe", lambda config, **kwargs: sample())
    return state, evidence


def test_emergency_created_after_validation_is_never_cleared(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    artifact = evidence_naming(state, evidence / "named.json")
    expected = guard.parse_evidence(artifact.read_bytes(), state)["clears"]
    (state / "emergency").write_text('{"at": 9.0, "reason": "unnamed emergency"}')
    with pytest.raises(SystemExit): guard.rearm({}, restoration=True, request=dict(at=guard.time.time()), expected=expected)
    assert (state / "emergency").exists()
    assert guard.read(state / "tripped")["reason"] == "host pressure"
    assert not (state / "restoring").exists()


def test_recreated_latch_with_same_reason_is_not_the_validated_latch(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    artifact = evidence_naming(state, evidence / "named.json")
    expected = guard.parse_evidence(artifact.read_bytes(), state)["clears"]
    (state / "tripped").unlink()
    guard.latch(state, "host pressure", None)
    with pytest.raises(SystemExit): guard.rearm({}, restoration=True, request=dict(at=guard.time.time()), expected=expected)
    assert (state / "tripped").exists()


def test_latch_content_changed_during_clearing_is_put_back(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    expected = guard.latches_present(state)
    real_replace = guard.os.replace
    def swap(src, dst):
        if pathlib.Path(src) == state / "tripped":
            (state / "tripped").write_text('{"at": 1.0, "reason": "swapped"}')
        real_replace(src, dst)
    monkeypatch.setattr(guard.os, "replace", swap)
    with pytest.raises(SystemExit): guard.clear_expected_latches(expected)
    assert guard.read(state / "tripped")["reason"] == "swapped"


@pytest.mark.parametrize("content", ["", "[]", "null", '{"reason": "missing time"}', '{"reason": "x", "at": NaN}'])
def test_malformed_latch_refuses_with_systemexit_and_changes_nothing(tmp_path, monkeypatch, content):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    (state / "tripped").write_text(content)
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"clears": {}, "verification": ["x"]}')
    with pytest.raises(SystemExit): guard.parse_evidence(artifact.read_bytes(), state)
    with pytest.raises(SystemExit): guard.clear_expected_latches({"tripped": dict(reason="x", at=1)})
    assert (state / "tripped").read_text() == content
    assert not (state / "restoring").exists()


def test_restore_refuses_evidence_outside_root_and_through_symlink(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    outside = tmp_path / "outside.json"
    outside.write_text(evidence_naming(state, tmp_path / "scratch.json").read_text())
    config = dict(evidence_root=str(evidence), life_support=dict(units=[]), resume_units=[], stop_cgroups=["a"])
    with pytest.raises(SystemExit): guard.restore(config, outside)
    link = evidence / "link.json"
    link.symlink_to(outside)
    with pytest.raises(SystemExit): guard.restore(config, link)
    assert guard.read(state / "tripped")["reason"] == "host pressure"




def test_empty_expected_latches_refused_before_any_change(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    with pytest.raises(SystemExit):
        guard.rearm({}, restoration=True, request=dict(at=guard.time.time()), expected={})
    assert guard.read(state / "tripped")["reason"] == "host pressure"
    assert not (state / "restoring").exists()


def test_emergency_latch_blocks_restoration_and_is_never_touched(tmp_path, monkeypatch):
    state = tmp_path / "state"; state.mkdir()
    monkeypatch.setattr(guard, "STATE", state)
    guard.latch(state, "host pressure", None)
    (state / "emergency").write_text('{"at": 2.0, "reason": "sentinel missing"}')
    named = evidence_naming(state, tmp_path / "both.json")
    with pytest.raises(SystemExit): guard.parse_evidence(named.read_bytes(), state)
    assert guard.read(state / "emergency")["reason"] == "sentinel missing"


def test_failed_put_back_falls_back_to_exclusive_write(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    expected = guard.latches_present(state)
    real = guard.latch_identity
    def mismatch(path):
        if pathlib.Path(path).name.startswith("tripped-"):
            raise SystemExit("controlled mismatch")
        return real(path)
    monkeypatch.setattr(guard, "latch_identity", mismatch)
    monkeypatch.setattr(guard.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("link refused")))
    with pytest.raises(SystemExit): guard.clear_expected_latches(expected)
    assert guard.read(state / "tripped")["reason"] == "host pressure"


def test_double_failure_latches_with_the_original_location_and_admission_closed(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    run = tmp_path / "run"
    guard.latch(state, "host pressure", None)
    expected = guard.latches_present(state)
    real = guard.latch_identity
    def mismatch(path):
        if pathlib.Path(path).name.startswith("tripped-"):
            raise SystemExit("controlled mismatch")
        return real(path)
    monkeypatch.setattr(guard, "latch_identity", mismatch)
    monkeypatch.setattr(guard.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("link refused")))
    monkeypatch.setattr(guard, "write_exclusive", lambda *a, **k: (_ for _ in ()).throw(OSError("write refused")))
    with pytest.raises(SystemExit):
        guard.rearm({}, restoration=True, request=dict(at=guard.time.time()), expected=expected)
    assert not (state / "restoring").exists()
    assert guard.read(state / "tripped")["reason"].startswith("latch clearing failed")
    assert not guard.admitted(state, run, now=guard.time.time(), unit="android-body-genome.service")


def test_total_put_back_failure_keeps_restore_marker_and_admission_closed(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    run = tmp_path / "run"
    guard.latch(state, "host pressure", None)
    expected = guard.latches_present(state)
    real = guard.latch_identity
    def mismatch(path):
        if pathlib.Path(path).name.startswith("tripped-"):
            raise SystemExit("controlled mismatch")
        return real(path)
    monkeypatch.setattr(guard, "latch_identity", mismatch)
    monkeypatch.setattr(guard.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("link refused")))
    monkeypatch.setattr(guard, "write_exclusive", lambda *a, **k: (_ for _ in ()).throw(OSError("write refused")))
    monkeypatch.setattr(guard, "latch", lambda *a, **k: (_ for _ in ()).throw(OSError("latch refused")))
    with pytest.raises(RuntimeError):
        guard.rearm({}, restoration=True, request=dict(at=guard.time.time()), expected=expected)
    assert (state / "restoring").exists()
    assert not guard.admitted(state, run, now=guard.time.time(), unit="android-body-genome.service")


def test_ordinary_rearm_refuses_while_a_restoration_marker_exists(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    guard.atomic(state / "restoring", dict(at=guard.time.time(), next_unit="android-body-genome.service"))
    with pytest.raises(SystemExit): guard.rearm({})
    assert guard.read(state / "tripped")["reason"] == "host pressure"


def test_emergency_appearing_mid_clear_is_never_cleared(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    expected = guard.latches_present(state)
    real = guard.latch_identity
    def appear(path):
        if pathlib.Path(path).name.startswith("tripped-"):
            (state / "emergency").write_text('{"at": 9.0, "reason": "unnamed emergency"}')
        return real(path)
    monkeypatch.setattr(guard, "latch_identity", appear)
    with pytest.raises(SystemExit): guard.clear_expected_latches(expected)
    assert guard.read(state / "emergency")["reason"] == "unnamed emergency"
    assert guard.read(state / "tripped")["reason"] == "host pressure"


def test_put_back_never_overwrites_a_different_tripped_latch(tmp_path, monkeypatch):
    state, evidence = _fresh_restore_state(tmp_path, monkeypatch)
    guard.latch(state, "host pressure", None)
    expected = guard.latches_present(state)
    real = guard.latch_identity
    def interloper(path):
        if pathlib.Path(path).name.startswith("tripped-"):
            (state / "tripped").write_text('{"at": 9.0, "reason": "interloper"}')
            raise SystemExit("controlled mismatch")
        return real(path)
    monkeypatch.setattr(guard, "latch_identity", interloper)
    with pytest.raises(SystemExit): guard.clear_expected_latches(expected)
    assert guard.read(state / "tripped")["reason"] == "interloper"
    assert list(state.glob("tripped-*.json"))


def test_failed_write_leaves_no_truncated_latch(tmp_path, monkeypatch):
    target = tmp_path / "tripped"
    monkeypatch.setattr(guard.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk gone")))
    with pytest.raises(OSError): guard.write_exclusive(target, b'{"reason": "partial"}')
    assert not target.exists()


def _proc(root, pid, parent, cgroup):
    directory = root / str(pid)
    directory.mkdir(parents=True)
    (directory / "cgroup").write_text(f"0::{cgroup}\n")
    (directory / "status").write_text(f"Name: x\nPPid: {parent}\n")


def test_caller_in_mishe_slice_is_refused_through_any_ancestor(tmp_path, monkeypatch):
    root = tmp_path / "proc"
    _proc(root, 1, 0, "/init.scope")
    _proc(root, 100, 1, "/user.slice/app.slice/operator-rescue.scope")
    _proc(root, 200, 100, "/user.slice/mishe.slice/mishe-lifeline.slice/tmux-spawn-x.scope")
    _proc(root, 300, 200, "/user.slice/mishe.slice/mishe-lifeline.slice/tmux-spawn-x.scope")
    monkeypatch.setattr(guard.os, "getpid", lambda: 300)
    assert guard.caller_inside_mishe(root)


def test_human_shell_outside_mishe_is_allowed(tmp_path, monkeypatch):
    root = tmp_path / "proc"
    _proc(root, 1, 0, "/init.scope")
    _proc(root, 100, 1, "/user.slice/app.slice/operator-rescue.scope")
    _proc(root, 300, 100, "/user.slice/app.slice/operator-rescue.scope")
    monkeypatch.setattr(guard.os, "getpid", lambda: 300)
    assert not guard.caller_inside_mishe(root)


def test_unreadable_process_chain_is_treated_as_inside(tmp_path, monkeypatch):
    monkeypatch.setattr(guard.os, "getpid", lambda: 4242)
    assert guard.caller_inside_mishe(tmp_path / "missing")


POLICY_IO_MAX = "252:0 rbps=64000000 wbps=16000000 riops=max wiops=max"


def test_io_max_with_an_extra_device_line_still_enforces_the_policy():
    live = POLICY_IO_MAX + "\n259:1 rbps=64000000 wbps=16000000 riops=max wiops=max"
    assert guard.io_max_ok(POLICY_IO_MAX, live)


def test_io_max_with_an_extra_device_above_the_ceiling_is_not_accepted():
    live = POLICY_IO_MAX + "\n259:1 rbps=max wbps=max riops=max wiops=max"
    assert not guard.io_max_ok(POLICY_IO_MAX, live)


def test_io_max_with_a_weakened_named_device_is_not_accepted():
    live = "252:0 rbps=128000000 wbps=16000000 riops=max wiops=max"
    assert not guard.io_max_ok(POLICY_IO_MAX, live)


def test_io_max_missing_the_named_device_is_not_accepted():
    live = "259:1 rbps=64000000 wbps=16000000 riops=max wiops=max"
    assert not guard.io_max_ok(POLICY_IO_MAX, live)


def test_io_max_with_a_tighter_extra_device_is_accepted():
    live = POLICY_IO_MAX + "\n259:1 rbps=32000000 wbps=8000000 riops=max wiops=max"
    assert guard.io_max_ok(POLICY_IO_MAX, live)


def test_a_changed_non_io_limit_is_still_tripped(tmp_path):
    (tmp_path / "memory.max").write_text("23622320128")
    assert guard.limits_match(tmp_path, {"memory.max": "23622320128"})
    assert not guard.limits_match(tmp_path, {"memory.max": "1"})


def test_prepare_writes_each_io_device_on_its_own(tmp_path, monkeypatch):
    """The io controller rejects several device lines in one write."""
    base = tmp_path / "mishe.slice"
    base.mkdir()
    (base / "cgroup.subtree_control").write_text("")
    written = []
    real_write_text = pathlib.Path.write_text

    def record(self, data, *args, **kwargs):
        if self.name == "io.max":
            written.append(data)
            return len(data)
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", record)
    config = dict(cgroup=str(base), limits={str(base): {"io.max": "252:0 rbps=64000000 wbps=16000000 riops=max wiops=max\n259:1 rbps=64000000 wbps=16000000 riops=max wiops=max"}})
    guard.prepare(config)
    assert written == ["252:0 rbps=64000000 wbps=16000000 riops=max wiops=max\n",
                       "259:1 rbps=64000000 wbps=16000000 riops=max wiops=max\n"]


def _life_support_watch(tmp_path, monkeypatch, life_state, *, ticks):
    """Run the watch loop for a fixed number of ticks with a given session state.

    The startup latch keeps its own reason, so the session latch is read from
    the reasons the loop offers, not from the ``tripped`` file's contents.
    """
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    monkeypatch.setattr(guard, "STATE", state)
    monkeypatch.setattr(guard, "RUN", run)
    monkeypatch.setattr(guard, "observe", lambda config: sample())
    monkeypatch.setattr(guard, "stop", lambda config: [])
    monkeypatch.setattr(guard, "notify", lambda message: None)
    offered = []
    real_latch = guard.latch
    def record(target, why, observation, life_support=None):
        offered.append(why)
        real_latch(target, why, observation, life_support=life_support)
    monkeypatch.setattr(guard, "latch", record)
    monkeypatch.setattr(guard, "ensure_life_support",
                        lambda config, start=False: dict(state=life_state))
    clock = {"now": 1000.0}
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock["now"])
    remaining = {"n": ticks}
    def tick(_):
        remaining["n"] -= 1
        clock["now"] += 5
        if remaining["n"] <= 0:
            raise InterruptedError
    monkeypatch.setattr(guard.time, "sleep", tick)
    with pytest.raises(InterruptedError):
        guard.watch({})
    return state, run, offered


def test_one_unusable_session_sample_does_not_latch_the_plant_off(tmp_path, monkeypatch):
    """A pane that exits between the tmux listing and the /proc read is not a dead session."""
    state, run, offered = _life_support_watch(tmp_path, monkeypatch, "UNKNOWN", ticks=2)
    assert "protected recovery session missing, dead or unobservable" not in offered
    row = guard.read(run / "status.json")
    assert row["life_support"]["missing_for_seconds"] < guard.LIFE_MISSING_SECONDS


def test_a_session_that_stays_missing_still_latches_the_plant_off(tmp_path, monkeypatch):
    """The tolerance must not disarm the detector for a session that is really gone."""
    state, run, offered = _life_support_watch(tmp_path, monkeypatch, "RED", ticks=8)
    assert "protected recovery session missing, dead or unobservable" in offered


def test_a_recovered_session_clears_the_missing_interval(tmp_path, monkeypatch):
    """A session that reads live again must not carry an old gap into a later failure."""
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    monkeypatch.setattr(guard, "STATE", state)
    monkeypatch.setattr(guard, "RUN", run)
    monkeypatch.setattr(guard, "observe", lambda config: sample())
    monkeypatch.setattr(guard, "stop", lambda config: [])
    monkeypatch.setattr(guard, "notify", lambda message: None)
    life = {"state": "UNKNOWN"}
    monkeypatch.setattr(guard, "ensure_life_support", lambda config, start=False: dict(life))
    clock = {"now": 1000.0}
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock["now"])
    ticks = {"n": 0}
    def tick(_):
        ticks["n"] += 1
        clock["now"] += 5
        # One bad sample, then the session reads live again.
        life["state"] = "UNKNOWN" if ticks["n"] == 1 else "PANES_LIVE"
        if ticks["n"] >= 3:
            raise InterruptedError
    monkeypatch.setattr(guard.time, "sleep", tick)
    with pytest.raises(InterruptedError):
        guard.watch({})
    assert "protected recovery session missing, dead or unobservable" not in guard.read(state / "tripped")["reason"]


def test_cpu_stat_reads_the_plants_slice_and_is_none_when_unreadable(tmp_path):
    plants = tmp_path / guard.PLANTS_SLICE
    plants.mkdir()
    (plants / "cpu.stat").write_text("usage_usec 1\nnr_periods 601\nnr_throttled 549\nthrottled_usec 9\n")
    assert guard.cpu_stat({"cgroup": str(tmp_path)}) == (601, 549)
    assert guard.cpu_stat({"cgroup": str(tmp_path / "missing")}) is None
    (plants / "cpu.stat").write_text("nr_periods x\n")
    assert guard.cpu_stat({"cgroup": str(tmp_path)}) is None


def test_cpu_throttle_is_the_throttled_share_over_the_trailing_window():
    # Two samples 60 s apart: 549 of 601 periods throttled, as measured by Health.
    throttle = guard.cpu_throttle([(0.0, 100, 50), (60.0, 701, 599)], now=60.0)
    assert throttle == dict(seconds=60.0, periods=601, throttled=549, ratio=0.9135)


def test_cpu_throttle_uses_only_samples_inside_the_window():
    samples = [(0.0, 0, 0), (100.0, 1000, 0), (130.0, 1300, 300)]
    throttle = guard.cpu_throttle(samples, now=130.0, window=60)
    assert throttle["seconds"] == 30.0 and throttle["ratio"] == 1.0


@pytest.mark.parametrize("samples", [
    [],
    [(0.0, 10, 1)],
    [(0.0, 10, 1), (5.0, 10, 1)],
    [(0.0, 10, 5), (5.0, 20, 4)],
    [(0.0, 20, 1), (5.0, 10, 1)],
    [(5.0, 10, 1), (5.0, 20, 2)],
])
def test_cpu_throttle_is_unknown_without_a_usable_span(samples):
    assert guard.cpu_throttle(samples, now=5.0) is None


def test_sustained_cpu_throttling_warns_and_routes_without_tripping(tmp_path, monkeypatch):
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir(); run.mkdir()
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    monkeypatch.setattr(guard, "observe", lambda config: sample())
    monkeypatch.setattr(guard, "stop", lambda config: [])
    monkeypatch.setattr(guard, "notify", lambda message: None)
    monkeypatch.setattr(guard, "ensure_life_support", lambda config, start=False: None)
    readings = iter([(0, 0), (200, 180), (400, 370)])
    monkeypatch.setattr(guard, "cpu_stat", lambda config: next(readings))
    ticks = iter([0, 1, 2, 3])
    def end(_):
        if next(ticks) == 2:
            raise InterruptedError
    monkeypatch.setattr(guard.time, "sleep", end)
    with pytest.raises(InterruptedError): guard.watch({})
    status = guard.read(run / "status.json")
    # The sentinel start latch is the only trip; throttling adds none.
    assert guard.read(state / "tripped")["reason"] == "sentinel started/restarted: explicit rearm required"
    assert status["cpu_throttle_warning"] is True
    assert status["cpu_throttle"]["periods"] == 400 and status["cpu_throttle"]["throttled"] == 370
    assert status["cpu_throttle"]["ratio"] == 0.925
    events = list(state.glob("cpu-throttle-*.json")); assert len(events) == 1
    event = guard.read(events[0])
    assert event["warning"] is True and event["resolver"] == "genome"


def test_trip_record_keeps_the_protected_session_observation(tmp_path):
    life = dict(state="PANES_LIVE", panes={"genome.1": {"pid": 7, "live": True, "engine": True}}, missing_since=1.0)
    guard.latch(tmp_path, "protected recovery session missing, dead or unobservable", sample(), life_support=life)
    record = guard.read(tmp_path / "tripped")
    assert record["life_support"] == life
    assert record["observation"] == sample()


def test_latch_without_life_observation_keeps_the_original_shape(tmp_path):
    guard.latch(tmp_path, "resource trip", sample())
    assert guard.read(tmp_path / "tripped")["life_support"] is None


def test_life_support_key_changes_only_with_observed_pane_identity():
    live = {"state": "PANES_LIVE", "missing_for_seconds": 0,
            "panes": {"health.1": {"pid": 10, "live": True, "engine": True, "command": "claude"}}}
    same_but_older = dict(live, missing_for_seconds=3, panes={"health.1": {"pid": 10, "live": True, "engine": True, "command": "claude"}})
    replaced = dict(live, panes={"health.1": {"pid": 11, "live": True, "engine": True, "command": "claude"}})
    assert guard.life_support_key(live) == guard.life_support_key(same_but_older)
    assert guard.life_support_key(live) != guard.life_support_key(replaced)
    assert guard.life_support_key(None) is None


def test_life_support_state_change_is_recorded_durably(tmp_path):
    life = {"state": "PANES_LIVE", "panes": {"genome.0": {"pid": 1, "live": True, "engine": True}}}
    guard.record_life_support(tmp_path, life)
    records = list(tmp_path.glob("life-support-*.json"))
    assert len(records) == 1
    assert guard.read(records[0])["life_support"] == life
