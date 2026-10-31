"""The restore's own gate must be observable, and its silent band must be visible.

On 2026-10-30 the plant sat latched for hours while every visible signal said
healthy.  The guard's ``clear_since`` is a ``time.monotonic()`` stamp, not
elapsed seconds, so it read as a large number and looked like a long-held
interval.  The real interval was being reset, and the reset happened in a band
where the guard's own warning never fires: the gate refuses at
``io_pressure >= 10``, the warning fires at ``io_pressure >= 25``, and pressure
receipts are only written when the warning *changes*.  So a host in the band
fails the restore's 120-second stability gate with nothing reported anywhere.

These tests pin the detector that closes that gap.  Each one is written so
that deleting the behaviour it names makes it fail: the states are
distinguished by their exit code and their reported state, not by a string
that happens to appear somewhere in a longer message.
"""
import importlib.util
import json
import pathlib
import time

import pytest

CHECK = pathlib.Path(__file__).resolve().parents[1] / "checks" / "restore-readiness.py"


def load_check():
    spec = importlib.util.spec_from_file_location("restore_readiness", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub_host(monkeypatch, check, *, observation=None, clear_since=None, latches=None,
              status_at=None, config=None, effective_slice="mishe-lifeline.slice",
              condition_result="yes", slice_dropin=None, condition_dropin=None,
              asks_lifeline=True):
    """Give the detector a complete, controlled host."""
    now = time.time()
    observation = dict(observation or {})
    observation.setdefault("limits_ok", True)
    observation.setdefault("ssh_available", True)
    observation.setdefault("host_io_available", True)
    observation.setdefault("io_pressure", 0.0)
    observation.setdefault("memory_pressure", 0.0)
    observation.setdefault("memory", 2 * 1024 ** 3)
    observation.setdefault("maximum", 22 * 1024 ** 3)
    observation.setdefault("tasks", 50)
    observation.setdefault("task_max", 1152)
    observation.setdefault("available", 27 * 1024 ** 3)
    observation.setdefault("disk_free", 588 * 1024 ** 3)

    status = {"at": now if status_at is None else status_at,
              "state": "TRIPPED" if latches else "READY",
              "mode": "LIFE_SUPPORT" if latches else "NORMAL",
              "clear_since": clear_since,
              "pressure_warning": False,
              "observation": observation,
              "life_support": {"state": "PANES_LIVE"}}

    monkeypatch.setattr(check, "GUARD_RUN", check.pathlib.Path("/nonexistent-run"))
    if config is None:
        config = {"resume_units": ["mishe-self-development-current-health.service"],
                  "life_support": {"units": ["mishe-self-development-current-health.service"]}}
    monkeypatch.setattr(check, "_read_json", lambda path: (
        status if path.name == "status.json" else
        (config if path.name == "mishe-containment.json" else {})))
    monkeypatch.setattr(check, "GUARD_STATE", check.pathlib.Path("/nonexistent-state"))

    latch_dir = {}
    for name in (latches or ()):
        latch_dir[name] = (latches[name] if isinstance(latches, dict)
                           else {"at": 1.0, "reason": name})

    monkeypatch.setattr(check, "_latch_names", lambda: sorted(latch_dir))
    monkeypatch.setattr(check, "_latch_identity",
                        lambda name: dict(latch_dir[name]) if name in latch_dir
                        else {"at": 1.0, "reason": name})
    monkeypatch.setattr(check, "_systemctl_show",
                        lambda unit, *props: {"Slice": effective_slice,
                                              "ConditionResult": condition_result})
    monkeypatch.setattr(check, "_winning_dropins",
                        lambda unit, key: (slice_dropin if key == "Slice" else condition_dropin) or {})
    monkeypatch.setattr(check, "_effective_dropin_order",
                        lambda unit: [("zzzzzz-host-containment.conf", 0,
                                       "mishe-restore-2147b.service.d", None)])
    monkeypatch.setattr(check, "_asks_for_lifeline_slice", lambda unit: asks_lifeline)
    return latch_dir


def test_a_latched_host_with_a_held_interval_is_ready(monkeypatch):
    """The healthy latched state: the gate is satisfied, so a restore would pass."""
    check = load_check()
    latches = stub_host(monkeypatch, check, clear_since=time.monotonic() - 500,
                        latches={"tripped"})
    row = check.assess()
    assert row["state"] == "ready"
    assert row["stability_satisfied"] is True
    assert row["continuous_healthy_seconds"] >= 120
    assert row["latches_present"] == ["tripped"]
    assert row["blocking_fields"] == []


def test_an_unheld_interval_is_blocked_not_ready(monkeypatch):
    """A latched host whose interval has not reached 120s is not restorable."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 30, latches={"tripped"})
    row = check.assess()
    assert row["state"] == "blocked"
    assert row["stability_satisfied"] is False


def test_the_interval_is_measured_from_the_monotonic_stamp_not_the_raw_number(monkeypatch):
    """clear_since is a monotonic stamp; reading it as elapsed seconds is the bug.

    A stamp of 14278 looks like "14278 seconds of health" to a reader who
    assumes it is a duration.  The detector must measure the age of the stamp.
    """
    check = load_check()
    stamp = time.monotonic() - 400
    stub_host(monkeypatch, check, clear_since=stamp, latches={"tripped"})
    row = check.assess()
    assert row["clear_since"] == stamp
    assert row["continuous_healthy_seconds"] < 500
    assert row["stability_satisfied"] is True


def test_the_silent_band_is_reported_and_not_hidden(monkeypatch):
    """The gap this detector exists for.

    The gate refuses at io_pressure >= 10; the warning fires at >= 25.  A host
    in the band fails the gate while pressure_warning is false, and no receipt
    is written.  The detector must say so rather than reporting a pass.
    """
    check = load_check()
    stub_host(monkeypatch, check, observation={"io_pressure": 13.0},
              clear_since=time.monotonic() - 500, latches={"tripped"})
    row = check.assess()
    assert row["state"] == "blocked"
    assert row["pressure_warning"] is False
    assert row["silent_band"] is True
    assert any("io_pressure" in field for field in row["blocking_fields"])


def test_the_memory_pressure_silent_band_is_also_reported(monkeypatch):
    """memory_pressure has the same shape of gap: the gate refuses at 1."""
    check = load_check()
    stub_host(monkeypatch, check, observation={"memory_pressure": 1.31},
              clear_since=time.monotonic() - 500, latches={"tripped"})
    row = check.assess()
    assert row["state"] == "blocked"
    assert row["silent_band"] is True
    assert any("memory_pressure" in field for field in row["blocking_fields"])


def test_a_healthy_host_reports_no_silent_band(monkeypatch):
    """The detector must not cry wolf: a clean host is clean."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500, latches={"tripped"})
    row = check.assess()
    assert row["silent_band"] is False
    assert row["blocking_fields"] == []


def test_a_reset_interval_is_blocked_and_reported_as_no_interval(monkeypatch):
    """A reset is clear_since = None: no interval is being held at all."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=None, latches={"tripped"})
    row = check.assess()
    assert row["state"] == "blocked"
    assert row["continuous_healthy_seconds"] is None
    assert row["stability_satisfied"] is False


def test_the_restore_unit_gated_on_the_latch_is_reported_as_a_path_problem(monkeypatch):
    """The unit whose job is to clear the latch must not be gated on it.

    The catch-all drop-in carries ConditionPathExists=!/...tripped.  When that
    is the value in force, the only unit that can clear the latch can never
    run, and the operator has to run it by hand instead.
    """
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500, latches={"tripped"},
              condition_result="no",
              condition_dropin={"file": "zzzzzz-host-containment.conf",
                                "directory": "mishe-.service.d",
                                "value": "!/var/lib/mishe-containment/tripped"})
    row = check.assess()
    assert any("gated on the latch not existing" in problem
               for problem in row["restore_unit_path_problems"])


def test_the_restore_unit_in_the_plant_slice_is_reported_as_a_path_problem(monkeypatch):
    """A restore unit placed in the plant slice is in the slice the guard kills."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500, latches={"tripped"},
              effective_slice="mishe-plants.slice",
              slice_dropin={"file": "zzzzzz-host-containment.conf",
                            "directory": "mishe-.service.d",
                            "value": "mishe-plants.slice"})
    row = check.assess()
    assert any("effective slice" in problem for problem in row["restore_unit_path_problems"])


def test_a_repaired_restore_path_is_not_reported_as_a_problem(monkeypatch):
    """After the repair the unit is in the lifeline slice with no latch condition."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500, latches={"tripped"},
              effective_slice="mishe-lifeline.slice", condition_result="yes",
              slice_dropin={"file": "zzzzzz-host-containment.conf",
                            "directory": "mishe-restore-2147b.service.d",
                            "value": "mishe-lifeline.slice"},
              condition_dropin={"file": "zzzzzz-host-containment.conf",
                                "directory": "mishe-restore-2147b.service.d",
                                "value": ""})
    row = check.assess()
    assert row["restore_unit_path_problems"] == []
    assert row["restore_unit_effective_slice"] == "mishe-lifeline.slice"


def test_a_stale_condition_result_alone_is_not_a_path_problem(monkeypatch):
    """systemd keeps the last evaluation's ConditionResult between evaluations.

    After a repair it still says "no" until the unit is started again, so a
    stale value alone must not be reported as the unit being broken.
    """
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500, latches={"tripped"},
              condition_result="no",
              condition_dropin={"file": "zzzzzz-host-containment.conf",
                                "directory": "mishe-restore-2147b.service.d",
                                "value": ""})
    row = check.assess()
    assert not any("ConditionResult" in problem
                   for problem in row["restore_unit_path_problems"])


def test_no_latch_is_reported_as_clear(monkeypatch):
    """A host with no latch needs no restore."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=None, latches=None)
    row = check.assess()
    assert row["state"] == "clear"
    assert row["latches_present"] == []


def test_an_emergency_latch_is_reported(monkeypatch):
    """An emergency latch is never cleared by a restore and must be visible."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=None,
              latches={"tripped", "emergency"})
    row = check.assess()
    assert row["emergency_latch"] is True
    assert row["latches_present"] == ["emergency", "tripped"]


def test_a_stale_sentinel_is_unknown_not_a_fault(monkeypatch):
    """A stale observation cannot decide the question."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500,
              latches={"tripped"}, status_at=time.time() - 600)
    with pytest.raises(check.Undecidable):
        check.assess()


def test_the_unknown_path_is_machine_readable(monkeypatch, capsys):
    """A monitor must learn the question was undecided without parsing prose."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500,
              latches={"tripped"}, status_at=time.time() - 600)
    assert check.main(["--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "UNKNOWN"
    assert payload["reason"]


def test_blocked_exits_two_and_ready_exits_zero(monkeypatch, capsys):
    """The exit code is the contract a scheduler reads."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500, latches={"tripped"},
              observation={"io_pressure": 13.0})
    assert check.main([]) == 2
    capsys.readouterr()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500, latches={"tripped"})
    assert check.main([]) == 0


def test_the_verdict_can_be_written_as_state(monkeypatch, tmp_path):
    """The state file is what the plant's other detectors read."""
    check = load_check()
    stub_host(monkeypatch, check, clear_since=time.monotonic() - 500, latches={"tripped"})
    target = tmp_path / "state.json"
    assert check.main(["--state", str(target)]) == 0
    payload = json.loads(target.read_text())
    assert payload["state"] == "ready"
    assert payload["stability_required_seconds"] == 120


def test_the_detector_runs_against_the_committed_tree():
    """The committed detector must run on this host and answer the question.

    It may report ready, blocked or clear: the host's state is not the test's
    business.  What the test pins is that it decides, or says UNKNOWN, rather
    than crashing.
    """
    import subprocess
    import sys
    result = subprocess.run([sys.executable, str(CHECK), "--json"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode in (0, 2, 3), result.stderr[-800:]
    payload = json.loads(result.stdout)
    assert payload["state"] in ("ready", "blocked", "clear", "UNKNOWN")


def test_the_gate_thresholds_come_from_the_installed_guard_when_readable():
    """The thresholds belong to the host's guard, not to this file.

    If the installed guard changes them, the detector must follow, or it will
    report a gate the host does not use.
    """
    check = load_check()
    source = check.GUARD_SOURCE
    if not source.exists():
        pytest.skip("the guard is not installed on this host")
    text = source.read_text(encoding="utf-8")
    import re
    match = re.search(r"^STABLE_SECONDS\s*=\s*([0-9.]+)", text, re.MULTILINE)
    assert match, "the installed guard no longer names STABLE_SECONDS"
    assert check._guard_constant("STABLE_SECONDS", 120) == float(match.group(1))
