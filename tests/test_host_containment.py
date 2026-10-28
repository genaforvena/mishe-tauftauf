"""The host safety boundary must fail closed independently of plant history."""
import threading
import time
import sys
import os
import pathlib

import pytest

from mishe_tauftauf import host_containment as guard


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
    diagnosis = evidence / "diagnosis.json"; diagnosis.write_text('{"verified": true}')
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    guard.atomic(run / "status.json", dict(at=guard.time.time(), state="READY"))
    monkeypatch.setattr(guard, "rearm", lambda config, **kwargs: None)
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
    with pytest.raises(OSError): guard.rearm({}, restoration=True, request=dict(at=guard.time.time()))
    assert guard.read(state / "tripped")["reason"] == "original failure"
    assert not guard.admitted(state, run)


def test_life_observation_requires_both_roles_and_their_actual_cgroup(tmp_path, monkeypatch):
    group = next(line[3:] for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
    lines = "".join(f"{role}\t{index}\t0\t{os.getpid()}\t{tmp_path}\n" for role in ("health", "genome") for index in (0, 1))
    monkeypatch.setattr(guard, "bounded_command", lambda *args, **kwargs: (0, lines.encode()))
    config = dict(user="test", life_support=dict(socket="unused", session="life", home=str(tmp_path), cgroup=group, roles=["health", "genome"]))
    assert guard.life_observation(config)["state"] == "PANES_LIVE"
    config["life_support"]["cgroup"] = "/not-this-process"
    assert guard.life_observation(config)["state"] == "RED"


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
    diagnosis = evidence / "diagnosis.json"; diagnosis.write_text('{"verified": true}')
    monkeypatch.setattr(guard, "STATE", state); monkeypatch.setattr(guard, "RUN", run)
    guard.latch(state, "original failure", None)
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
