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


def test_command_that_cannot_run_leaves_signal_untouched_and_fails(home):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    before = (home / "signals" / "devto.json").read_bytes()
    assert producers.run(home, "devto", ["/nonexistent/check"], now=NOW + 5, timeout=10) == 2
    assert (home / "signals" / "devto.json").read_bytes() == before
    assert store.read(home, "devto", now=NOW + 5000).state == "UNKNOWN"      # ages out; never fakes GREEN


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
        assert store.read(home, "devto", now=NOW + 5).value == "GREEN"      # untouched, will age
    assert store.read(home, "devto", now=NOW + 5000).state == "UNKNOWN"


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


def test_ci_pending_is_no_observation_and_unknown_is_a_failure(tmp_path):
    store.register(tmp_path, "ci", max_age=900, subscribers=["genome"])
    producers.produce(tmp_path, "ci", now=NOW, read=lambda h: {"state": "pass"})
    assert producers.produce(tmp_path, "ci", now=NOW + 400, read=lambda h: {"state": "pending"}) is None
    assert store.read(tmp_path, "ci", now=NOW + 400).value == "GREEN"       # untouched, not refreshed
    with pytest.raises(producers.ObservationFailed):
        producers.produce(tmp_path, "ci", now=NOW + 500, read=lambda h: {"state": "unknown"})
    assert store.read(tmp_path, "ci", now=NOW + 5000).state == "UNKNOWN"


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


def test_cannot_tell_exit_journals_the_check_output(home, capsys):
    producers.run(home, "devto", [PY, "-c", "pass"], now=NOW, timeout=10)
    assert producers.run(home, "devto", [PY, "-c", "import sys; print('guard row X'); sys.exit(3)"],
                         now=NOW + 1, timeout=10) == 2
    assert "exit=3" in capsys.readouterr().err
