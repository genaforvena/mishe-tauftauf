"""signal-coverage: the signal of signals — every registered signal has a live producer,
a subscriber and a response; UNKNOWN when coverage itself cannot be judged."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
from datetime import datetime, timezone
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
    home, sigdir = site(tmp_path, {"phones": {**register_entry(), "reoffer": 3600.0}})
    observed(sigdir, "phones", value=None, state="UNKNOWN", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    dispositions.set_disposition(home, "phones", "known", "senses", "redmi10 stale",
                                 ttl=3600, now=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert not any("phones" in c for c in report["causes"])
    assert any("dispositioned known by senses" in n for n in report["counts"]["notes"])


def test_operator_parked_role_answers_its_own_ignored_signal(tmp_path, monkeypatch):
    """A role intentionally absent (parked:<name>) is read back for its signal: a past-bound
    UNKNOWN is a note, not an ignored cause (audio-hint-livingroom, DM #300947)."""
    _clear_systemctl(monkeypatch)
    from mishe_tauftauf import dispositions
    home, sigdir = site(tmp_path, {"mic": {**register_entry(), "reoffer": 3600.0}})
    observed(sigdir, "mic", value=None, state="UNKNOWN", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    dispositions.park(home, "mic", "operator", "DM #300947 listener stays disabled",
                      ttl=3600, now=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert not any("mic" in c for c in report["causes"])
    assert any("dispositioned parked by operator" in n for n in report["counts"]["notes"])


def test_expired_disposition_returns_the_ignored_finding(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    from mishe_tauftauf import dispositions
    home, sigdir = site(tmp_path, {"phones": register_entry()})
    observed(sigdir, "phones", value=None, state="UNKNOWN", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    dispositions.set_disposition(home, "phones", "known", "senses", "redmi10 stale",
                                 ttl=600, now=NOW - 3600)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    cause = [c for c in report["causes"] if c.startswith("ignored") and "phones" in c]
    assert cause, report["causes"]
    # The finding returns with the resolver and deadline named, so no one has to
    # open the disposition file to learn who owes the recheck (budget-opencode-go,
    # 2026-11-20 11:38Z).
    due = datetime.fromtimestamp(NOW - 3600 + 600, timezone.utc)
    assert f"known disposition by senses lapsed {due:%H:%M:%SZ}" in cause[0]


def test_known_disposition_without_recheck_wake_is_a_cause(tmp_path, monkeypatch):
    """2026-11-20 budget-claude: a recorded answer is only an answer if its recheck can
    wake someone. settle (yielding.py:220) arms the recheck only with a reoffer and a
    subscriber; without them the disposition's deadline lapses unserved while this check
    still reports the finding as dispositioned."""
    _clear_systemctl(monkeypatch)
    from mishe_tauftauf import dispositions
    home, sigdir = site(tmp_path, {"phones": register_entry()})  # no reoffer
    turned = NOW - 3 * MAX_AGE
    observed(sigdir, "phones", "RED", at=turned, seen=NOW - 60)
    wake_file(home, "steward", turned + 60, yield_at=turned + 120)
    dispositions.set_disposition(home, "phones", "known", "steward", "forecast",
                                 ttl=3600, now=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("unserveable-disposition: phones") for c in report["causes"])
    assert not any(c.startswith("ignored: phones") for c in report["causes"])
    assert report["counts"]["unserveable"] == 1


def test_servable_known_disposition_stays_only_a_note(tmp_path, monkeypatch):
    """With reoffer and a subscriber the recheck can be armed, so the disposition still
    answers the RED; the new cause must not double-count or narrow existing coverage."""
    _clear_systemctl(monkeypatch)
    from mishe_tauftauf import dispositions
    assert not mod._recheck_servable({"reoffer": 3600.0, "subscribers": []})
    assert mod._recheck_servable({"reoffer": 3600.0, "subscribers": ["steward"]})
    home, sigdir = site(tmp_path, {"phones": {**register_entry(), "reoffer": 3600.0}})
    turned = NOW - 3 * MAX_AGE
    observed(sigdir, "phones", "RED", at=turned, seen=NOW - 60)
    wake_file(home, "steward", turned + 60, yield_at=turned + 120)
    dispositions.set_disposition(home, "phones", "known", "steward", "forecast",
                                 ttl=3600, now=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert not any("phones" in c for c in report["causes"])
    assert report["counts"]["unserveable"] == 0
    assert any("dispositioned known by steward" in n for n in report["counts"]["notes"])


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
    (sigdir / "repo-change-cursors.json").write_text("{}")   # subscriber cursor, never a signal
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert report["counts"]["unheard"] == 1
    assert "legacy-orphan" in report["detail"]
    assert "repo-change-cursors" not in report["detail"]


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


def provider_record(home, accounts, *, age=60):
    path = home / "operator" / "autonomy-repair-20261106" / "provider-budget-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": "RED", "reason": "", "accounts": accounts}))
    stamp = NOW - age
    os.utime(path, (stamp, stamp))


def _covered_site(tmp_path):
    home, sigdir = site(tmp_path, {"doctor": register_entry()})
    observed(sigdir, "doctor", seen=NOW - 60)
    return home, sigdir


def test_red_provider_verdict_without_a_signal_is_dark(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = _covered_site(tmp_path)
    provider_record(home, {"codex-a": {"state": "RED",
                                      "reason": "7d RUNS OUT BEFORE RESET (67% used)"}})
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    assert any(c.startswith("dark-provider-verdict: codex-a")
               and "no registered signal budget-codex" in c
               and "67% used" in c for c in report["causes"])
    assert report["counts"]["dark_providers"] == 1


def test_dispositioned_dark_verdict_names_owner_and_keeps_red(tmp_path, monkeypatch):
    from mishe_tauftauf import dispositions
    _clear_systemctl(monkeypatch)
    home, sigdir = _covered_site(tmp_path)
    provider_record(home, {"codex-a": {"state": "RED",
                                      "reason": "7d RUNS OUT BEFORE RESET (68% used)"}})
    dispositions.set_disposition(home, "budget-codex", "known", "steward",
                                 "weekly forecast", ttl=86400, now=NOW)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    cause = [c for c in report["causes"] if c.startswith("dark-provider-verdict: codex-a")][0]
    assert "dispositioned known by steward" in cause
    assert "no registered signal budget-codex" in cause
    assert "register it with a reoffer" not in cause
    assert report["counts"]["dark_providers"] == 1


def test_expired_dark_disposition_restores_register_instruction(tmp_path, monkeypatch):
    from mishe_tauftauf import dispositions
    _clear_systemctl(monkeypatch)
    home, sigdir = _covered_site(tmp_path)
    provider_record(home, {"codex-a": {"state": "RED", "reason": "7d RUNS OUT BEFORE RESET"}})
    dispositions.set_disposition(home, "budget-codex", "known", "steward",
                                 "old forecast", ttl=60, now=NOW - 120)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 2
    cause = [c for c in report["causes"] if c.startswith("dark-provider-verdict: codex-a")][0]
    assert "register it with a reoffer" in cause
    assert "dispositioned known by" not in cause


def test_registered_provider_signal_covers_the_verdict(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    registry = {"doctor": register_entry(),
                "budget-codex": register_entry(registered_at=NOW - 60)}
    home, sigdir = site(tmp_path, registry)
    observed(sigdir, "doctor", seen=NOW - 60)
    provider_record(home, {"codex-a": {"state": "RED", "reason": "forecast holds work"}})
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert report["counts"]["dark_providers"] == 0
    assert "codex-a=RED (signal budget-codex)" in report["detail"]


def test_stale_provider_record_is_a_note_not_a_finding(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = _covered_site(tmp_path)
    provider_record(home, {"codex-a": {"state": "RED", "reason": "old"}}, age=7200)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert any("provider verdicts unmeasured" in n for n in report["counts"]["notes"])
    assert not any(c.startswith("dark-provider-verdict") for c in report["causes"])


def test_green_unregistered_verdict_is_not_a_dark_lane(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = _covered_site(tmp_path)
    provider_record(home, {"codex-a": {"state": "GREEN", "reason": "windows satisfy policy"}})
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0
    assert "codex-a=GREEN (NO signal budget-codex)" in report["detail"]
    assert not any(c.startswith("dark-provider-verdict") for c in report["causes"])


def _two_ignored(tmp_path, *, second_at):
    """Two RED readings unchanged past the bound; the second's age is a parameter."""
    home, sigdir = site(tmp_path, {"alpha": register_entry(), "beta": register_entry()})
    observed(sigdir, "alpha", "RED", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    observed(sigdir, "beta", "RED", at=second_at, seen=NOW - 60)
    wake_file(home, "steward", NOW - 3 * MAX_AGE + 60, yield_at=NOW - 3 * MAX_AGE + 120)
    return home, sigdir


def _event_line(report):
    return [line for line in report["detail"].splitlines() if line.startswith(mod.EVENT_MARK)]


def test_event_is_stable_when_only_age_grows(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = _two_ignored(tmp_path / "a", second_at=NOW - 3 * MAX_AGE)
    _, first = mod.run(home, NOW, sigdir=sigdir)
    for name in ("alpha", "beta"):   # the producer keeps beating, so only the lapse ages
        os.utime(sigdir / f"{name}.seen", (NOW + 3540, NOW + 3540))
    _, later = mod.run(home, NOW + 3600, sigdir=sigdir)
    assert _event_line(first) and _event_line(first) == _event_line(later)


def test_event_moves_when_a_new_lapse_joins_the_red(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = _two_ignored(tmp_path / "b", second_at=NOW - 60)
    _, one = mod.run(home, NOW, sigdir=sigdir)
    observed(sigdir, "beta", "RED", at=NOW - 3 * MAX_AGE, seen=NOW - 60)
    _, two = mod.run(home, NOW, sigdir=sigdir)
    assert _event_line(one) != _event_line(two)
    assert len(two["causes"]) == 2 and len(one["causes"]) == 1


def test_event_moves_when_one_lapse_clears_and_another_keeps_red(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = _two_ignored(tmp_path / "c", second_at=NOW - 3 * MAX_AGE)
    _, both = mod.run(home, NOW, sigdir=sigdir)
    observed(sigdir, "beta", "GREEN", at=NOW - 60, seen=NOW - 60)
    _, one = mod.run(home, NOW, sigdir=sigdir)
    assert one["verdict"] == "RED" and _event_line(both) != _event_line(one)


def test_green_run_declares_no_event(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"ok": register_entry()})
    observed(sigdir, "ok", "GREEN", at=NOW - 60, seen=NOW - 60)
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert code == 0 and _event_line(report) == []


def test_null_registration_entry_is_unknown_not_a_green_coverage(tmp_path, monkeypatch):
    _clear_systemctl(monkeypatch)
    home, sigdir = site(tmp_path, {"ok": register_entry()})
    observed(sigdir, "ok", "GREEN", at=NOW - 60, seen=NOW - 60)
    registry = json.loads((sigdir / "registry.json").read_text())
    registry["broken"] = None
    (sigdir / "registry.json").write_text(json.dumps(registry))
    code, report = mod.run(home, NOW, sigdir=sigdir)
    assert report["verdict"] == "UNKNOWN" and "broken" in report["cause"]
    assert code == mod.UNKNOWN


def test_unknown_cli_prints_the_cause_and_exits_with_its_code(tmp_path, monkeypatch, capsys):
    _clear_systemctl(monkeypatch)
    home, _ = site(tmp_path, with_registry=False)
    assert mod.main(["--home", str(home)]) == 3
    out = capsys.readouterr().out
    assert "registry.json unreadable" in out and "signal-coverage: UNKNOWN" in out


def test_producer_reads_the_declared_event_and_keeps_it_out_of_the_reason(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from mishe_tauftauf import producers, signal_store
    detail = tmp_path / "x.detail"
    detail.write_text("signal-coverage: RED: ignored: alpha\nignored: alpha\nsignal-event: abc123def456aa11\n")
    from mishe_tauftauf import coverage_event
    assert coverage_event.declared_event("signal-coverage", detail) == "abc123def456aa11"
    # Only the declaring duty may declare: another check's prose line is never an event.
    assert coverage_event.declared_event("devto", detail) is None
    prose = tmp_path / "z.detail"
    prose.write_text("ok\nsignal-event: ordinary prose one\n")
    assert coverage_event.declared_event("devto", prose) is None
    plain = tmp_path / "y.detail"
    plain.write_text("check failed\n")
    assert coverage_event.declared_event("signal-coverage", plain) is None
    home = tmp_path / "home"
    home.mkdir()
    signal_store.register(home, "gamma", max_age=600.0, subscribers=("steward",), now=NOW)
    assert signal_store.observe(home, "gamma", "RED", now=NOW, source="t", event="e1", reason="r")
    assert not signal_store.observe(home, "gamma", "RED", now=NOW, source="t", event="e1", reason="r")
    assert signal_store.observe(home, "gamma", "RED", now=NOW, source="t", event="e2", reason="r")


def test_gate_skips_count_only_the_named_producer(monkeypatch):
    # A stale producer's gap is a containment skip when its own unit was skipped by the condition gate.
    # 'devto' must not absorb 'devto-article-duplication' skips (the unit dot keeps names apart).
    journal = "\n".join([
        "mishe-producer-devto-article-duplication.service - Mishe producer devto-article-duplication was skipped because of an unmet condition check (ConditionPathExists=!/var/lib/mishe-containment/tripped).",
        "mishe-producer-devto.service - Mishe producer devto was skipped because of an unmet condition check (ConditionPathExists=!/var/lib/mishe-containment/tripped).",
        "mishe-producer-signal-coverage.service - Mishe producer signal-coverage was skipped because of an unmet condition check (ConditionPathExists=!/var/lib/mishe-containment/tripped).",
        "mishe-producer-signal-coverage.service - Mishe producer signal-coverage started.",
    ])

    class Done:
        returncode = 0
        stdout = journal

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: Done())
    assert mod._gate_skips("devto", 0, 10) == 1
    assert mod._gate_skips("signal-coverage", 0, 10) == 1
    assert mod._gate_skips("observer-checks", 0, 10) == 0


def test_gate_skips_unreadable_journal_is_unknown(monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: Failed())
    assert mod._gate_skips("devto", 0, 10) is None
