"""The host safety boundary must fail closed independently of plant history."""
import threading
import time
import sys

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
    guard.atomic(run / "status.json", {"state": "TRIPPED", "at": guard.time.time()})
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
    def observation(_):
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


@pytest.mark.parametrize("field,value", [("units", None), ("limits", None), ("units", [1]), ("limits", {"/cg": None})])
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
    assert guard.bounded_command([sys.executable, "-c", "import time;time.sleep(60)"], timeout=.05) is None
    assert time.monotonic() - started < 1


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
    assert event["next_probe_seconds"] <= 5
    assert event["observation"]["io_pressure"] == 60
