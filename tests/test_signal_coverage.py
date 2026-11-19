"""signal-coverage: the signal of signals — every registered signal has a live producer,
a subscriber and a response; UNKNOWN when coverage itself cannot be judged."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "signal_coverage", Path(__file__).parent.parent / "checks" / "signal-coverage.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

NOW = 10_000_000.0
MAX_AGE = 1800.0


def site(tmp_path, registry=None, *, with_registry=True):
    sigdir = tmp_path / "signals"
    sigdir.mkdir(parents=True, exist_ok=True)
    if with_registry:
        (sigdir / "registry.json").write_text(json.dumps(registry or {}))
    return tmp_path, sigdir


def register_entry(max_age=MAX_AGE, subscribers=("steward",), registered_at=NOW - 3600):
    return {"max_age": max_age, "subscribers": list(subscribers),
            "registered_at": registered_at, "wake_on": "change"}


def observed(sigdir, name, value="GREEN", *, at=NOW - 60, state="OK", seen=None):
    (sigdir / f"{name}.json").write_text(json.dumps(
        {"event": None, "observed_at": at, "reason": "", "rev": 1, "source": "test",
         "state": state, "value": value}))
    (sigdir / f"{name}.seen").touch()
    stamp = seen if seen is not None else at
    os.utime(sigdir / f"{name}.seen", (stamp, stamp))


def _clear_systemctl(monkeypatch):
    monkeypatch.setenv("MISHE_SYSTEMCTL_BIN", "/nonexistent-systemctl")


def test_registry_unreadable_is_unknown(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, with_registry=False)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 3 and report["verdict"] == "UNKNOWN"


def test_green_when_every_registered_signal_is_fresh_and_answered(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"budget-opencode-go": register_entry(),
                                   "signal-coverage": register_entry()})
    observed(sigdir, "budget-opencode-go", seen=NOW - 60)
    observed(sigdir, "signal-coverage", seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0 and report["verdict"] == "GREEN"
    assert report["counts"]["ok"] == 2
    assert "budget-window-care: budget-opencode-go=GREEN" in report["detail"]


def test_never_observed_past_grace_is_unmeasured_red(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"orphan-duty": register_entry(registered_at=NOW - 10 * MAX_AGE)})
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("unmeasured: orphan-duty") for c in report["causes"])


def test_never_observed_inside_grace_is_a_note_not_a_cause(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"fresh-duty": register_entry(registered_at=NOW - MAX_AGE / 2)})
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert any("inside grace" in n for n in report["counts"]["notes"])


def test_stale_freshness_stamp_is_red(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"sleepy": register_entry()})
    observed(sigdir, "sleepy", seen=NOW - 5 * MAX_AGE)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("stale: sleepy") for c in report["causes"])


def test_producer_that_ran_on_time_with_stale_input_is_not_a_missed_cadence(tmp_path, monkeypatch):
    """household-phones: the producer runs every 2 min and exits UNKNOWN (input stale). The reading is
    untouched, so the freshness stamp ages; the cadence heartbeat keeps the producer judged on time, and
    the stale input is judged as an ignored UNKNOWN instead of a missed cadence."""
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"phones": register_entry()})
    observed(sigdir, "phones", value=None, state="UNKNOWN", at=NOW - 5 * MAX_AGE, seen=NOW - 5 * MAX_AGE)
    (sigdir / "phones.ran").touch()
    os.utime(sigdir / "phones.ran", (NOW - 60, NOW - 60))
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert not any(c.startswith("stale: phones") for c in report["causes"])
    assert any(c.startswith("ignored") and "phones" in c for c in report["causes"])
    assert code == 2


def test_no_heartbeat_still_reports_missed_cadence(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"sleepy": register_entry()})
    observed(sigdir, "sleepy", seen=NOW - 5 * MAX_AGE)
    (sigdir / "sleepy.ran").touch()
    os.utime(sigdir / "sleepy.ran", (NOW - 4 * MAX_AGE, NOW - 4 * MAX_AGE))
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert any(c.startswith("stale: sleepy") for c in report["causes"])


def wake_file(home, role, start, *, yield_at=None, output_at=None):
    """One wake record: the name carries the start; yield/output mtimes carry the end."""
    d = home / "wakes"
    d.mkdir(parents=True, exist_ok=True)
    key = f"wake-{role}-{int(start * 1e9)}"
    (d / f"{key}.prompt.md").write_text("dossier", encoding="utf-8")
    for suffix, at in ((".yield.json", yield_at), (".prompt.out.jsonl", output_at)):
        if at is not None:
            path = d / f"{key}{suffix}"
            path.write_text("{}", encoding="utf-8")
            os.utime(path, (at, at))


def test_red_reading_unchanged_past_bound_is_ignored(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"parked": register_entry()})
    observed(sigdir, "parked", "RED", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    wake_file(home, "steward", NOW - 3 * MAX_AGE + 60, yield_at=NOW - 3 * MAX_AGE + 120)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("ignored: parked") for c in report["causes"])
    assert not any(c.startswith("unattended-red") for c in report["causes"])


def test_unexpired_disposition_parks_an_ignored_reading(tmp_path, monkeypatch):
    """The resolver's recorded answer is read back: a past-bound UNKNOWN with an unexpired
    disposition is a note, not an ignored cause (household-phones was flagged despite one)."""
    _clear_systemctl(monkeypatch)
    from mishe_tauftauf import dispositions
    home, sigdir = site(tmp_path, {"phones": register_entry()})
    observed(sigdir, "phones", value=None, state="UNKNOWN", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    dispositions.set_disposition(home, "phones", "known", "senses", "redmi10 stale",
                                 ttl=3600, now=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert not any("phones" in c for c in report["causes"])
    assert any("dispositioned known by senses" in n for n in report["counts"]["notes"])


def test_expired_disposition_returns_the_ignored_finding(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    from mishe_tauftauf import dispositions
    home, sigdir = site(tmp_path, {"phones": register_entry()})
    observed(sigdir, "phones", value=None, state="UNKNOWN", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    dispositions.set_disposition(home, "phones", "known", "senses", "redmi10 stale",
                                 ttl=600, now=NOW - 3600)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert any(c.startswith("ignored") and "phones" in c for c in report["causes"])


def test_fresh_red_reading_is_pending_not_ignored(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"just-moved": register_entry()})
    observed(sigdir, "just-moved", "RED", at=NOW - 60, seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert any("response pending" in n for n in report["counts"]["notes"])


def test_self_stale_producer_is_named_red(tmp_path, monkeypatch):
    """Ilya DM #303234: the exercised stale-producer case — this producer missed its
    cadence, and the check must name itself RED instead of reporting coverage GREEN."""
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"signal-coverage": register_entry(),
                                    "healthy": register_entry()})
    observed(sigdir, "healthy", seen=NOW - 60)
    observed(sigdir, "signal-coverage", "GREEN", at=NOW - 60, seen=NOW - 10 * MAX_AGE)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("self stale") for c in report["causes"])
    assert report["self"].startswith("self stamp")


def test_self_before_first_registration_is_not_a_cause(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"healthy": register_entry()})
    observed(sigdir, "healthy", seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0 and "first run" in report["self"]


def test_orphan_signal_files_are_unheard_detail_not_cause(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"healthy": register_entry()})
    observed(sigdir, "healthy", seen=NOW - 60)
    observed(sigdir, "legacy-orphan")           # on disk, not in registry
    (sigdir / "units.memory.json").write_text("{}")   # unit memory, never a signal
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert report["counts"]["unheard"] == 1
    assert "legacy-orphan" in report["detail"]


def test_failing_producer_unit_is_red(tmp_path, monkeypatch):
    fake = tmp_path / "fake-systemctl"
    fake.write_text("#!/bin/sh\nif [ \"$1\" = \"--user\" ]; then "
                    "echo 'mishe-producer-x.service loaded failed failed'; exit 0; fi\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    monkeypatch.setenv("MISHE_SYSTEMCTL_BIN", str(fake))
    home, sigdir = site(tmp_path, {"healthy": register_entry()})
    observed(sigdir, "healthy", seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("failing-producer units") for c in report["causes"])


def test_unreadable_systemctl_is_reported_not_assumed(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"healthy": register_entry()})
    observed(sigdir, "healthy", seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert any("unit state unmeasured" in n for n in report["counts"]["notes"])


def test_unmeasured_budget_account_rides_in_the_report(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"healthy": register_entry()})
    observed(sigdir, "healthy", seen=NOW - 60)
    (home / "engine").mkdir()
    (home / "engine" / "budget.json").write_text(
        json.dumps({"accounts": {"go-a": {}, "mystery-b": {}}}))
    code, report = mod.run(home, NOW, sigdir=sigdir,
                           repo=Path(__file__).parent.parent)
    assert code == 0   # go-a is covered by the manifest; mystery-b is report-only
    assert report["unmeasured_accounts"] == ["mystery-b"]


def test_missing_budget_file_is_a_note_not_a_verdict(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"healthy": register_entry()})
    observed(sigdir, "healthy", seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir,
                           repo=Path(__file__).parent.parent)
    assert code == 0
    assert any("budget.json unmeasured" in n for n in report["counts"]["notes"])


def test_unattended_red_is_a_cause_naming_signal_and_owner(tmp_path, monkeypatch):
    """Ilya DM #303254: doctor RED stood with no owner wake — the class names both."""
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"doctor": register_entry(subscribers=("health",))})
    observed(sigdir, "doctor", "RED", at=NOW - 1200, seen=NOW - 60)
    (home / "wakes").mkdir()
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("unattended-red: doctor") and "health" in c
               for c in report["causes"])
    assert report["counts"]["unattended"] == 1


def test_unattended_red_past_the_ignored_bound_stays_unattended(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"doctor": register_entry(subscribers=("health",))})
    observed(sigdir, "doctor", "RED", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    (home / "wakes").mkdir()
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("unattended-red: doctor") for c in report["causes"])
    assert not any(c.startswith("ignored: doctor") for c in report["causes"])


def test_owner_wake_since_the_turn_attends_the_red(tmp_path, monkeypatch):
    """The clearing: once an owner wake crossed the turn, the accusation is gone."""
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"doctor": register_entry(subscribers=("health",))})
    observed(sigdir, "doctor", "RED", at=NOW - 1200, seen=NOW - 60)
    wake_file(home, "health", NOW - 600, yield_at=NOW - 540)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert not any(c.startswith("unattended-red") for c in report["causes"])
    assert any("response pending" in n for n in report["counts"]["notes"])


def test_owner_wake_running_across_the_turn_attends(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"doctor": register_entry(subscribers=("health",))})
    observed(sigdir, "doctor", "RED", at=NOW - 1200, seen=NOW - 60)
    wake_file(home, "health", NOW - 1800, output_at=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert not any(c.startswith("unattended-red") for c in report["causes"])


def test_owner_wake_ended_before_the_turn_does_not_attend(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"doctor": register_entry(subscribers=("health",))})
    observed(sigdir, "doctor", "RED", at=NOW - 1200, seen=NOW - 60)
    wake_file(home, "health", NOW - 3000, yield_at=NOW - 2400)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("unattended-red: doctor") for c in report["causes"])


def test_fresh_red_inside_15_min_is_pending_not_unattended(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"doctor": register_entry(subscribers=("health",))})
    observed(sigdir, "doctor", "RED", at=NOW - 600, seen=NOW - 60)
    (home / "wakes").mkdir()
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert not any(c.startswith("unattended-red") for c in report["causes"])


def test_unreadable_wake_evidence_is_a_note_not_an_accusation(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"doctor": register_entry(subscribers=("health",))})
    observed(sigdir, "doctor", "RED", at=NOW - 1200, seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)   # no wakes/ directory at all
    assert code == 0
    assert any("wake evidence unmeasured" in n for n in report["counts"]["notes"])
    assert not any(c.startswith("unattended-red") for c in report["causes"])


def test_own_red_reading_is_not_ignored_by_itself(tmp_path, monkeypatch):
    # The self reading is recomputed every run and judged by its own stamp (the self
    # check). Judging its previous RED as "ignored" locks the verdict RED: nothing can
    # clear it until the stale self reading does, and it never does.
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"signal-coverage": register_entry(max_age=3600)})
    observed(sigdir, "signal-coverage", "RED", at=NOW - 5 * 3600, seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert not any("signal-coverage reading" in c for c in report["causes"])
