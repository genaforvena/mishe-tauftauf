import sys

import pytest

from mishe_tauftauf import producers, signal_store as store

NOW = 7000.0
PY = sys.executable


@pytest.fixture
def home(tmp_path):
    store.register(tmp_path, "devto", max_age=900, subscribers=["pub"])
    return tmp_path


def test_exit_zero_is_green_and_nonzero_is_red_with_detail(home):
    assert producers.run(home, "devto", [PY, "-c", "print('fine')"], now=NOW, timeout=10) == 0
    assert store.read(home, "devto", now=NOW).value == "GREEN"
    assert producers.run(home, "devto", [PY, "-c", "import sys; print('stale feed'); sys.exit(2)"], now=NOW + 1, timeout=10) == 0
    sig = store.read(home, "devto", now=NOW + 1)
    assert sig.value == "RED"
    assert "stale feed" in (home / "signals" / "devto.detail").read_text()


def test_command_that_cannot_run_turns_the_last_green_unknown_and_fails(home):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    assert producers.run(home, "devto", ["/nonexistent/check"], now=NOW + 5, timeout=10) == 2
    signal = store.read(home, "devto", now=NOW + 5)
    assert signal.state == "UNKNOWN" and signal.value is None      # a failed look never leaves GREEN standing
    assert "not run" in signal.reason


def test_timeout_is_a_failed_observation_not_red_and_not_green(home):
    assert producers.run(home, "devto", [PY, "-c", "import time; time.sleep(30)"], now=NOW, timeout=0.5) == 2
    assert store.read(home, "devto", now=NOW).state == "UNKNOWN"


def test_dispatch_producer_red_when_a_dispatch_is_open_too_long(tmp_path):
    from mishe_tauftauf.core.interfaces import UNKNOWN
    from mishe_tauftauf.core.state import DispatchRecord, DispatchStore
    store.register(tmp_path, "dispatch", max_age=900, subscribers=["genome"])
    assert producers.dispatch(tmp_path, now=NOW, max_open=600) == "GREEN"
    DispatchStore(tmp_path / "dispatch").write(
        DispatchRecord(role="docs", key="k", state=UNKNOWN, detail="d"))
    path = tmp_path / "dispatch" / "docs.json"
    import os
    os.utime(path, (NOW - 1000, NOW - 1000))
    assert producers.dispatch(tmp_path, now=NOW, max_open=600) == "RED"
    assert producers.dispatch(tmp_path, now=NOW - 900 + 1, max_open=600) == "GREEN"


def test_dispatch_default_bound_is_the_engine_hard_cap_not_the_soft_timeout(tmp_path):
    from mishe_tauftauf.core.interfaces import UNKNOWN
    from mishe_tauftauf.core.state import DispatchRecord, DispatchStore
    import os
    from mishe_tauftauf.engine_router import DEFAULT_HARD_TIMEOUT_S
    assert producers.dispatch_open_bound(tmp_path) == DEFAULT_HARD_TIMEOUT_S + producers.DISPATCH_OPEN_GRACE_S
    DispatchStore(tmp_path / "dispatch").write(
        DispatchRecord(role="docs", key="k", state=UNKNOWN, detail="d"))
    path = tmp_path / "dispatch" / "docs.json"
    # a productive wake open past its 1800 s soft timeout, inside the hard cap: not stuck
    os.utime(path, (NOW - 1900, NOW - 1900))
    assert producers.dispatch(tmp_path, now=NOW) == "GREEN"
    # open past the hard cap plus grace: the engine bound failed, so it is stuck
    os.utime(path, (NOW - DEFAULT_HARD_TIMEOUT_S - producers.DISPATCH_OPEN_GRACE_S - 1,) * 2)
    assert producers.dispatch(tmp_path, now=NOW) == "RED"


def test_dispatch_bound_is_the_soft_deadline_when_the_route_cap_is_below_it(tmp_path):
    # hard_cap() returns None when hard_timeout_s <= the soft deadline: the engine cuts at the soft
    # deadline, so an open record inside soft + grace is still healthy.
    from mishe_tauftauf import units
    import os
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "routes.json").write_text('{"version": 1, "hard_timeout_s": 600, "roles": {}}')
    bound = producers.dispatch_open_bound(tmp_path)
    assert bound == units.WAKE_TIMEOUT + producers.DISPATCH_OPEN_GRACE_S
    from mishe_tauftauf.core.interfaces import UNKNOWN
    from mishe_tauftauf.core.state import DispatchRecord, DispatchStore
    DispatchStore(tmp_path / "dispatch").write(
        DispatchRecord(role="docs", key="k", state=UNKNOWN, detail="d"))
    path = tmp_path / "dispatch" / "docs.json"
    os.utime(path, (NOW - 2000, NOW - 2000))
    assert producers.dispatch(tmp_path, now=NOW) == "GREEN"
    os.utime(path, (NOW - bound - 1, NOW - bound - 1))
    assert producers.dispatch(tmp_path, now=NOW) == "RED"


def test_load_registers_declared_signals_and_returns_units_producers(home):
    (home / "producers.json").write_text(
        '[{"name":"egress","command":["/bin/true"],"every":300,"signal":{"max_age":900,"subscribers":["health"]}}]')
    loaded = producers.load(home, python=PY)
    assert [p.name for p in loaded] == ["egress"]
    assert store.registry(home)["egress"]["max_age"] == 900
    assert store.registry(home)["egress"]["reoffer"] == producers.DECLARED_REOFFER_SECONDS
    assert loaded[0].command[:5] == [PY, "-m", "mishe_tauftauf", "--home", str(home)]


def test_declared_command_runs_through_the_signal_run_adapter(home):
    (home / "producers.json").write_text(
        '[{"name":"egress","command":["/bin/true"],"every":300,"timeout":50,"signal":{"max_age":900,"subscribers":[]}}]')
    cmd = producers.load(home, python=PY)[0].command
    assert cmd[5:9] == ["signal", "run", "egress", "--timeout"] and "--red-codes" in cmd and cmd[-2:] == ["--", "/bin/true"]


def test_a_crash_or_unknown_exit_is_a_failed_observation_not_red(home):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    for code in (1, 3, 127):
        assert producers.run(home, "devto", [PY, "-c", f"import sys; sys.exit({code})"],
                             now=NOW + 5, timeout=10) == 2
        signal = store.read(home, "devto", now=NOW + 5)
        assert signal.state == "UNKNOWN" and f"check exit {code}" in signal.reason   # not GREEN, not RED
    assert store.read(home, "devto", now=NOW + 5000).state == "UNKNOWN"


def test_a_failed_look_is_unknown_and_a_later_real_green_recovers(home):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    assert producers.run(home, "devto", [PY, "-c", "import sys; sys.exit(1)"], now=NOW + 800, timeout=10) == 2
    assert store.read(home, "devto", now=NOW + 800).state == "UNKNOWN"
    assert producers.run(home, "devto", [PY, "-c", "pass"], now=NOW + 810, timeout=10) == 0
    assert store.read(home, "devto", now=NOW + 810).value == "GREEN"       # a real observation recovers it


def test_red_codes_are_declared_per_producer(home):
    assert producers.run(home, "devto", [PY, "-c", "import sys; sys.exit(7)"], now=NOW, timeout=10,
                         red_codes=(7,)) == 0
    assert store.read(home, "devto", now=NOW).value == "RED"


def test_declared_unknown_code_succeeds_and_leaves_signal_to_age(home):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    before = (home / "signals" / "devto.json").read_bytes()
    assert producers.run(home, "devto", [PY, "-c", "import sys; sys.exit(4)"], now=NOW + 5, timeout=10,
                         unknown_codes=(4,)) == 0
    assert (home / "signals" / "devto.json").read_bytes() == before
    assert store.read(home, "devto", now=NOW + 5000).state == "UNKNOWN"


def test_declared_unknown_code_logs_its_reason_to_the_journal(home, capsys):
    # A Mac-down camera exits 4 and writes its reason only to stdout; the journal must still carry it.
    script = "print('{\"reason_code\": \"mac_unreachable\"}'); import sys; sys.exit(4)"
    assert producers.run(home, "devto", [PY, "-c", script], now=NOW, timeout=10, unknown_codes=(4,)) == 0
    err = capsys.readouterr().err
    assert "devto: unknown=4 detail=" in err and "mac_unreachable" in err


def test_undeclared_code_stays_a_failure_even_with_unknown_codes_set(home):
    assert producers.run(home, "devto", [PY, "-c", "import sys; sys.exit(1)"], now=NOW, timeout=10,
                         unknown_codes=(4,)) == 2


def test_load_passes_unknown_codes_to_the_adapter(home):
    (home / "producers.json").write_text(
        '[{"name":"phones","command":["/bin/true"],"every":120,"timeout":30,'
        '"unknown_codes":[4],"signal":{"max_age":300,"subscribers":[]}}]')
    cmd = producers.load(home, python=PY)[0].command
    assert cmd[cmd.index("--unknown-codes") + 1] == "4"


def test_ci_producer_maps_actions_results(tmp_path):
    store.register(tmp_path, "ci", max_age=900, subscribers=["genome"])
    for state, expect in (("pass", "GREEN"), ("fail", "RED")):
        assert producers.produce(tmp_path, "ci", now=NOW, read=lambda h: {"state": state}) == expect
        assert store.read(tmp_path, "ci", now=NOW).value == expect


def test_ci_pending_reports_the_newest_finished_run_and_falls_back_when_none_finished(tmp_path):
    store.register(tmp_path, "ci", max_age=900, subscribers=["genome"])
    producers.produce(tmp_path, "ci", now=NOW, read=lambda h: {"state": "fail"})
    assert store.read(tmp_path, "ci", now=NOW).value == "RED"
    def pending(h):                      # the newest commit's run is still going
        return {"state": "pending"}
    # The failing commit is already answered by a newer green run: while the newest commit's run is
    # still going the producer reports the finished result, not the superseded RED.
    assert producers.produce(tmp_path, "ci", now=NOW + 400, read=pending,
                             completed=lambda h: {"state": "pass"}) == "GREEN"
    assert store.read(tmp_path, "ci", now=NOW + 400).value == "GREEN"
    assert producers.produce(tmp_path, "ci", now=NOW + 800, read=pending,
                             completed=lambda h: {"state": "fail"}) == "RED"
    # Nothing on the branch has finished yet: keep the standing value fresh instead of expiring it.
    assert producers.produce(tmp_path, "ci", now=NOW + 1200, read=pending,
                             completed=lambda h: {"state": "pending"}) is None
    assert store.read(tmp_path, "ci", now=NOW + 1500).value == "RED"      # refreshed freshness: no expiry
    with pytest.raises(producers.ObservationFailed):
        producers.produce(tmp_path, "ci", now=NOW + 500, read=lambda h: {"state": "unknown"})
    assert store.read(tmp_path, "ci", now=NOW + 5000).state == "UNKNOWN"


def test_ci_pending_binds_the_real_reader_when_none_is_injected(tmp_path, monkeypatch):
    """The default must name ci_watch.read_completed; every other test injects one, which hid a
    TypeError('NoneType' object is not callable) on the live pending path (producer crashed 02:25Z)."""
    store.register(tmp_path, "ci", max_age=900, subscribers=["genome"])
    from mishe_tauftauf import ci_watch
    monkeypatch.setattr(ci_watch, "read_completed", lambda home: {"state": "pass"})
    assert producers.produce(tmp_path, "ci", now=NOW, read=lambda h: {"state": "pending"}) == "GREEN"
    assert store.read(tmp_path, "ci", now=NOW).value == "GREEN"


def test_ci_unknown_failure_carries_the_reader_detail(tmp_path):
    store.register(tmp_path, "ci", max_age=900, subscribers=["genome"])
    with pytest.raises(producers.ObservationFailed, match="CI state 'unknown': gh: rate limited"):
        producers.produce(tmp_path, "ci", now=NOW, read=lambda h: {"state": "unknown", "detail": "gh: rate limited"})


def test_tape_lock_is_green_when_free_and_red_when_held_throughout(tmp_path):
    import fcntl
    (tmp_path / "chat.log").write_text("")
    assert producers.tape_lock(tmp_path, now=NOW, attempts=2, pause=0.01) == "GREEN"
    with (tmp_path / ".seed.lock").open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        assert producers.tape_lock(tmp_path, now=NOW, attempts=3, pause=0.01) == "RED"


def test_a_check_that_cannot_start_or_times_out_journals_why(home, capsys):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    assert producers.run(home, "devto", ["/nonexistent/check"], now=NOW + 1, timeout=10) == 2
    assert "devto: not run: EngineNotStarted" in capsys.readouterr().err
    assert producers.run(home, "devto", [PY, "-c", "import time; time.sleep(30)"], now=NOW + 2, timeout=0.5) == 2
    assert "devto: not run: EngineTimeout: exceeded 0.5s" in capsys.readouterr().err


def test_silent_cannot_tell_exit_says_it_had_no_output(home, capsys):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    assert producers.run(home, "devto", [PY, "-c", "import sys; sys.exit(3)"], now=NOW + 1, timeout=10) == 2
    err = capsys.readouterr().err
    assert "devto: exit=3 detail=(no output on stdout or stderr)" in err
    assert "detail=b''" not in err


def test_cannot_tell_exit_journals_the_check_output(home, capsys):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    assert producers.run(home, "devto", [PY, "-c", "import sys; print('guard row X'); sys.exit(3)"],
                         now=NOW + 1, timeout=10) == 2
    assert "exit=3" in capsys.readouterr().err


def test_delivery_requests_duty_is_green_idle_and_red_for_standing_faults(tmp_path):
    import json
    from mishe_tauftauf import delivery_requests as dr
    assert producers.delivery_requests(tmp_path, now=NOW) == "GREEN"
    rows = tmp_path / "work/delivery-requests"
    rows.mkdir(parents=True)
    (rows / "done.json").write_text(json.dumps({"state": "delivered", "error": ""}))
    assert producers.delivery_requests(tmp_path, now=NOW) == "GREEN"
    (rows / "stuck.json").write_text(json.dumps({"state": "UNKNOWN", "error": "effect unknown"}))
    assert dr.standing_problem(tmp_path, NOW) == "effect unknown"
    assert producers.delivery_requests(tmp_path, now=NOW) == "RED"
    (rows / "stuck.json").unlink()
    (tmp_path / "work/delivery-admission.json").write_text(json.dumps({"id": "x", "pid": 2**30, "at": NOW - 3600}))
    assert "dead owner" in dr.standing_problem(tmp_path, NOW)
    assert producers.delivery_requests(tmp_path, now=NOW) == "RED"
