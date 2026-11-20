"""The boot-admission detector must name a stuck boot, not a pile of stale signals.

On 2026-11-20 the one-shot boot authorization expired. A separate recovery
cleared the latch before producers resumed. The alert must distinguish a
latched host from a producer outage after the guard has recovered.
"""
import importlib.util
import json
import pathlib

import pytest

CHECK = pathlib.Path(__file__).resolve().parents[1] / "checks" / "boot-admission.py"


def load_check():
    spec = importlib.util.spec_from_file_location("boot_admission", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub_host(monkeypatch, check, *, admit_active="inactive", admit_result="",
              admit_status="", latches=(), producers=0, sentinel="READY",
              mode="NORMAL", age=1800, sample_age=1):
    now = 1_795_188_000
    status = {"state": sentinel, "mode": mode}
    if sample_age is not None:
        status["at"] = now - sample_age
    admit = {"LoadState": "loaded", "ActiveState": admit_active,
             "Result": admit_result, "ExecMainStatus": admit_status,
             "ConditionResult": "yes"}
    monkeypatch.setattr(check, "_boot_id", lambda: "test-boot-id")
    monkeypatch.setattr(check, "_boot_age", lambda: age)
    monkeypatch.setattr(check.time, "time", lambda: now)
    monkeypatch.setattr(check, "_latches", lambda: list(latches))
    monkeypatch.setattr(check, "_read_json", lambda path: status)
    monkeypatch.setattr(check, "_systemctl_show", lambda *a, **k: admit)
    monkeypatch.setattr(check, "_producer_count", lambda: producers)


def test_healthy_boot_is_green(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, producers=12)
    row = check.assess()
    assert row["state"] == "green"
    assert "12 producer(s) running or completed this boot" in row["detail"]


def test_expired_authorization_is_red_and_names_human_action(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, admit_active="failed", admit_result="exit-code",
              admit_status="3", latches=("tripped",), sentinel="TRIPPED",
              mode="LIFE_SUPPORT", producers=0)
    row = check.assess()
    assert row["state"] == "red"
    assert "failed" in row["detail"]
    assert "HUMAN ROOT ACTION REQUIRED" in row["detail"]
    assert "does NOT clear or renew" in row["detail"]
    assert check.HUMAN_ACTION == row["human_action"]


def test_expired_authorization_after_rearm_routes_producer_outage_to_health(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, admit_active="failed", admit_result="exit-code",
              admit_status="3", producers=0)
    row = check.assess()
    assert row["state"] == "red"
    assert "user producer timers" in row["detail"]
    assert "exit-code" in row["detail"]
    assert row["human_action"] is None


def test_a_latch_beats_earlier_successful_producers(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, latches=("tripped",), producers=12)
    row = check.assess()
    assert row["state"] == "red"
    assert "tripped" in row["detail"]


@pytest.mark.parametrize("sample_age", [None, 21, -1])
def test_unusable_guard_timestamp_stays_unknown(monkeypatch, capsys, sample_age):
    check = load_check()
    stub_host(monkeypatch, check, producers=12, sample_age=sample_age)
    assert check.main([]) == 3
    assert "UNKNOWN" in capsys.readouterr().out


def test_latched_after_grace_with_no_producers_is_red(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, latches=("tripped",), producers=0, age=900,
              sentinel="TRIPPED", mode="LIFE_SUPPORT")
    row = check.assess()
    assert row["state"] == "red"
    assert "tripped" in row["detail"]


def test_latch_within_grace_is_unknown_not_red(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, latches=("tripped",), producers=0, age=90,
              sentinel="TRIPPED", mode="LIFE_SUPPORT")
    row = check.assess()
    assert row["state"] == "unknown"


def test_unreadable_sentinel_is_unknown(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, producers=12)

    def fail(_path):
        raise check.Undecidable("cannot read status.json: gone")

    monkeypatch.setattr(check, "_read_json", fail)
    with pytest.raises(check.Undecidable):
        check.assess()


@pytest.mark.parametrize("kwargs,code", [
    (dict(producers=12), 0),
    (dict(admit_active="failed", admit_result="exit-code", admit_status="3"), 2),
    (dict(admit_active="failed", sentinel="TRIPPED", mode="LIFE_SUPPORT"), 3),
    (dict(latches=("tripped",), producers=0, age=900, sentinel="TRIPPED",
          mode="LIFE_SUPPORT"), 2),
    (dict(latches=("tripped",), producers=0, age=90, sentinel="TRIPPED",
          mode="LIFE_SUPPORT"), 3),
])
def test_main_exit_codes(monkeypatch, kwargs, code):
    check = load_check()
    stub_host(monkeypatch, check, **kwargs)
    assert check.main([]) == code


def test_green_still_notes_a_stale_failed_admit_unit(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, producers=5, admit_active="failed",
              admit_result="exit-code", admit_status="3")
    row = check.assess()
    assert row["state"] == "green"
    assert "still failed (result exit-code, status 3)" in row["detail"]
    assert row["human_action"] is None


def test_state_file_written_json(monkeypatch, tmp_path):
    check = load_check()
    stub_host(monkeypatch, check, admit_active="failed", admit_result="exit-code",
              admit_status="3")
    target = tmp_path / "sub" / "state.json"
    assert check.main(["--state", str(target)]) == 2
    row = json.loads(target.read_text())
    assert row["state"] == "red"
    assert row["boot_admit_status"] == "3"


def test_oneshot_producers_that_ran_this_boot_count_as_admitted(monkeypatch):
    """Producers are oneshot timers: between runs they are inactive.

    Counting only active units read 0 on a healthy plant and sent a false RED
    at 15:25Z on 2026-11-20 while six producers had just succeeded.  A success
    this boot counts; a failed run and a never-run unit do not.
    """
    check = load_check()
    listing = ("mishe-producer-commit-risk.service loaded inactive dead x\n"
               "mishe-producer-bound-evidence.service loaded failed failed x\n"
               "mishe-producer-never.service loaded inactive dead x\n"
               "mishe-producer-running.service loaded active running x\n")
    shown = ("Id=mishe-producer-commit-risk.service\nActiveState=inactive\n"
             "Result=success\nExecMainExitTimestampMonotonic=5000000\n\n"
             "Id=mishe-producer-bound-evidence.service\nActiveState=failed\n"
             "Result=exit-code\nExecMainExitTimestampMonotonic=6000000\n\n"
             "Id=mishe-producer-never.service\nActiveState=inactive\n"
             "Result=success\nExecMainExitTimestampMonotonic=0\n\n"
             "Id=mishe-producer-running.service\nActiveState=activating\n"
             "Result=success\nExecMainExitTimestampMonotonic=0\n")
    calls = []

    class Done:
        def __init__(self, out):
            self.stdout, self.stderr, self.returncode = out, "", 0

    def fake_run(command, **kwargs):
        calls.append(command)
        return Done(listing if "list-units" in command else shown)

    monkeypatch.setattr(check.subprocess, "run", fake_run)
    assert check._producer_count() == 2
