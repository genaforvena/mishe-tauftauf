"""Installing the guard is not activating it: the running sentinel must have loaded it.

The guard is a long-running process.  Replacing the file on disk leaves the
running boundary executing the code it loaded at start, so an installed fix
can sit unexercised while the plant is latched off by the old behaviour.
These tests pin the check that tells the two states apart.
"""
import datetime
import importlib.util
import pathlib
import subprocess

import pytest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "guard-activation" / "check-activation.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("check_activation", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_sentinel_that_started_before_the_bytes_was_written_is_not_activated(tmp_path, monkeypatch):
    """The case that latched the plant: the file was replaced, the process was not restarted."""
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    monkeypatch.setattr(tool, "_process_started",
                        lambda pid: datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60))
    row = tool.report()
    assert row["activated"] is not True
    assert "before" in row["reason"]


def test_a_sentinel_that_started_after_the_bytes_was_written_is_activated(tmp_path, monkeypatch):
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    monkeypatch.setattr(tool, "_process_started",
                        lambda pid: datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60))
    row = tool.report()
    assert row["activated"] is True


def test_a_missing_sentinel_is_not_activated(tmp_path, monkeypatch):
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: None)
    row = tool.report()
    assert row["activated"] is not True
    assert "no running main process" in row["reason"]


def test_a_missing_installed_file_is_not_activated(tmp_path, monkeypatch):
    tool = load_tool()
    monkeypatch.setattr(tool, "GUARD_PATH", tmp_path / "absent.py")
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    row = tool.report()
    assert row["activated"] is not True
    assert "missing or unreadable" in row["reason"]


def test_an_unreadable_start_time_is_not_activated(tmp_path, monkeypatch):
    """An unknown start time must not be read as an activation."""
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    monkeypatch.setattr(tool, "_process_started", lambda pid: None)
    row = tool.report()
    assert row["activated"] is not True
    assert "start time is unreadable" in row["reason"]


def test_the_exit_code_is_zero_only_when_activated(tmp_path, monkeypatch, capsys):
    tool = load_tool()
    guard = tmp_path / "guard.py"
    guard.write_bytes(b"new bytes\n")
    monkeypatch.setattr(tool, "GUARD_PATH", guard)
    monkeypatch.setattr(tool, "_main_pid", lambda: 4242)
    monkeypatch.setattr(tool, "_process_started",
                        lambda pid: datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60))
    assert tool.main([]) == 1
    assert "activated" in capsys.readouterr().out


def test_the_apply_tool_reports_activation_after_a_write(tmp_path, monkeypatch):
    """An install must not be readable as a live change on its own."""
    apply_tool = pathlib.Path(__file__).resolve().parents[1] / "tools" / "guard-activation" / "apply.py"
    source = apply_tool.read_text(encoding="utf-8")
    assert "check-activation.py" in source
    assert "the running sentinel has not loaded these bytes" in source


def _pointer(name):
    """The commit a pointer names, exactly as the tool reads it.

    ``apply.py`` reads the file's bytes without stripping them, so the
    pointer's own bytes are part of the contract: a trailing newline would
    make the name unresolvable.  This helper returns the raw text so a test
    can assert on that, not just on a stripped name.
    """
    commits = pathlib.Path(__file__).resolve().parents[1] / "tools" / "guard-activation" / "commits"
    return (commits / (name + ".commit")).read_text(encoding="utf-8")


def test_the_pointer_files_name_commits_without_stray_whitespace():
    """A pointer is a commit name and nothing else.

    ``apply.py`` passes the file's contents straight to ``git show``, so a
    trailing newline or surrounding space would make the name unresolvable
    and every install would fail with a git error rather than a refusal the
    operator could read.
    """
    for name in ("install_commit", "revert_commit"):
        raw = _pointer(name)
        assert raw == raw.strip(), f"{name} has surrounding whitespace"
        assert "\n" not in raw, f"{name} must name one commit on one line"
        assert raw, f"{name} must name a commit"


def test_the_install_target_is_a_descendant_of_the_revert_target():
    """The install target must be able to carry the revert target's behaviour.

    The revert target is the bytes that are on the host; the install target is
    what replaces them.  The install target has to descend from the revert
    target, so that reverting puts back bytes that are a strict ancestor of
    what was installed rather than an unrelated file.  This is checked
    against the committed tree only, so it does not depend on the host's
    installed bytes and it holds in a shallow clone.
    """
    repo = pathlib.Path(__file__).resolve().parents[1]
    revert = _pointer("revert_commit")
    install = _pointer("install_commit")
    assert revert != install, "the two pointers must name different commits"

    def ancestry(older, newer):
        result = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer],
                                capture_output=True)
        if result.returncode == 128:
            pytest.skip("the repository history needed for this check is not available here")
        return result.returncode == 0

    assert ancestry(revert, install), (
        "the install target must descend from the revert target; a revert would "
        "otherwise replace the host's guard with unrelated bytes")


def test_the_install_target_tolerates_one_unusable_protected_session_sample(tmp_path, monkeypatch):
    """The install target must require a continuous gap before it latches the plant off.

    A single unusable protected-session sample latched the whole plant off
    three times in one day.  The install target has to require the session to
    read as missing for a continuous interval before it stops the plant, or
    the causal repair is not what gets installed.

    The check is behavioural, run against the committed bytes of the install
    target: the watch loop must offer the latch after a session that stays
    missing, and must not offer it after a single bad sample.  It reads only
    the committed tree, so it holds in a shallow clone.
    """
    repo = pathlib.Path(__file__).resolve().parents[1]
    source = "src/mishe_tauftauf/host_containment.py"
    install = _pointer("install_commit")
    raw = subprocess.run(["git", "-C", str(repo), "show", f"{install}:{source}"],
                         capture_output=True)
    if raw.returncode:
        pytest.skip("the committed bytes of the install target are not available here")
    body = raw.stdout
    assert b"LIFE_MISSING_SECONDS" in body, "the install target must debounce the session latch"

    target = tmp_path / "guard_install_target.py"
    target.write_bytes(body)
    spec = importlib.util.spec_from_file_location("guard_install_target", target)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    # The guard module patches the shared ``time`` module.  The clock is
    # replaced through ``monkeypatch`` so it is restored afterwards; a direct
    # assignment would leave a frozen clock behind for every later test.
    clock = {"now": 1000.0}
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock["now"])

    def offered(life_state, ticks):
        """The latch reasons the watch loop offers for one session reading."""
        import tempfile
        reasons = []
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state, run = root / "state", root / "run"
            state.mkdir()
            run.mkdir()
            guard.STATE = state
            guard.RUN = run
            guard.observe = lambda config: dict(
                oom_kill=0, memory=1, maximum=2, tasks=1, task_max=2, available=1,
                disk_free=1, memory_pressure=0.0, io_pressure=0.0, limits_ok=True,
                ssh_available=True, host_io_available=True, probes={})
            guard.stop = lambda config: []
            guard.notify = lambda message: None
            real_latch = guard.latch

            def record(target_state, why, observation):
                reasons.append(why)
                real_latch(target_state, why, observation)

            guard.latch = record
            guard.ensure_life_support = lambda config, start=False: dict(state=life_state)
            remaining = {"n": ticks}

            def tick(_):
                remaining["n"] -= 1
                clock["now"] += 5
                if remaining["n"] <= 0:
                    raise InterruptedError

            monkeypatch.setattr(guard.time, "sleep", tick)
            with pytest.raises(InterruptedError):
                guard.watch({})
        return reasons

    trip = "protected recovery session missing, dead or unobservable"
    assert trip not in offered("UNKNOWN", 2), (
        "the install target latched the plant off after a single unusable "
        "protected-session sample, which is the trip cause being repaired")
    assert trip in offered("RED", 10), (
        "the install target must still latch a session that stays missing")


def _apply_tool():
    """The install tool, loaded from its path."""
    tool = pathlib.Path(__file__).resolve().parents[1] / "tools" / "guard-activation" / "apply.py"
    spec = importlib.util.spec_from_file_location("apply_tool", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_pointer_verdict_names_each_state_the_install_can_be_in():
    """The drift has to stay observable after the install, not only before it.

    A verdict that only holds in the pre-install state would go quiet exactly
    when the repair has landed.  Each of the three states a host can be in --
    carrying the revert target, carrying the install target, or carrying
    bytes from neither -- must be named, and the third must not be reported
    as a state from which a write can succeed.
    """
    verdict = _apply_tool().pointer_verdict
    before, after = b"revert bytes", b"install bytes"

    at_revert = verdict(before, before, after)
    assert "revert target" in at_revert

    at_install = verdict(after, before, after)
    assert "install target" in at_install

    drifted = verdict(b"someone else's bytes", before, after)
    assert "neither pointer" in drifted

    assert "no guard is installed" in verdict(None, before, after)


def test_the_install_and_the_revert_are_a_round_trip():
    """The backout path must restore exactly the bytes the install replaced.

    An install that cannot be undone is not a repair, it is a change of the
    host's safety boundary with no way back.  The tool refuses to write
    anything but the bytes it expects, so the pair of actions has to accept
    the installed bytes in turn and put back the original ones.
    """
    tool = _apply_tool()
    before, after = b"revert bytes", b"install bytes"

    expected, replacement = tool.write_plan("apply", before, before, after)
    assert expected == before, "an install must expect the revert target's bytes"
    assert replacement == after, "an install must write the install target's bytes"

    expected, replacement = tool.write_plan("revert", after, before, after)
    assert expected == after, "a revert must expect the install target's bytes"
    assert replacement == before, "a revert must write back the revert target's bytes"


def test_a_write_is_refused_when_the_installed_bytes_are_not_the_expected_ones():
    """Bytes from neither pointer must not be replaced.

    The refusal is the property that keeps an unrelated edit on the host
    from being overwritten, so it is checked against the same decision the
    tool makes.
    """
    tool = _apply_tool()
    before, after = b"revert bytes", b"install bytes"
    drifted = b"someone else's bytes"

    expected, _ = tool.write_plan("apply", drifted, before, after)
    assert drifted != expected, "a drifted host must not match the expected bytes"

    expected, _ = tool.write_plan("revert", drifted, before, after)
    assert drifted != expected, "a drifted host must not match the expected bytes"
