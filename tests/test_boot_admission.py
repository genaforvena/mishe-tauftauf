"""The boot-admission detector must name a stuck boot, not a pile of stale signals.

On 2026-11-20 the host rebooted, the one-shot boot authorization expired during
the outage, and no producer started.  signal-coverage went RED only through
about sixty stale stamps.  These tests pin the detector that closes that gap:
each state is distinguished by its reported state and exit code, so deleting
the behaviour it names makes the test fail.
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
              mode="NORMAL", age=1800):
    status = {"state": sentinel, "mode": mode}
    admit = {"LoadState": "loaded", "ActiveState": admit_active,
             "Result": admit_result, "ExecMainStatus": admit_status,
             "ConditionResult": "yes"}
    monkeypatch.setattr(check, "_boot_id", lambda: "test-boot-id")
    monkeypatch.setattr(check, "_boot_age", lambda: age)
    monkeypatch.setattr(check, "_latches", lambda: list(latches))
    monkeypatch.setattr(check, "_read_json", lambda path: status)
    monkeypatch.setattr(check, "_systemctl_show", lambda *a, **k: admit)
    monkeypatch.setattr(check, "_producer_count", lambda: producers)


def test_healthy_boot_is_green(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, producers=12)
    row = check.assess()
    assert row["state"] == "green"
    assert "admitted" in row["detail"]


def test_expired_authorization_is_red_and_names_human_action(monkeypatch):
    check = load_check()
    stub_host(monkeypatch, check, admit_active="failed", admit_result="exit-code",
              admit_status="3", sentinel="READY", mode="NORMAL", producers=0)
    row = check.assess()
    assert row["state"] == "red"
    assert "failed" in row["detail"]
    assert "HUMAN ROOT ACTION REQUIRED" in row["detail"]
    assert "does NOT clear or renew" in row["detail"]
    assert check.HUMAN_ACTION == row["human_action"]


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
    assert "note" in row["detail"]


def test_state_file_written_json(monkeypatch, tmp_path):
    check = load_check()
    stub_host(monkeypatch, check, admit_active="failed", admit_result="exit-code",
              admit_status="3")
    target = tmp_path / "sub" / "state.json"
    assert check.main(["--state", str(target)]) == 2
    row = json.loads(target.read_text())
    assert row["state"] == "red"
    assert row["boot_admit_status"] == "3"
