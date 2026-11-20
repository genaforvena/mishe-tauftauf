"""The next-due churn detector pairs every timer start with the due handler run it was meant to cause."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_check():
    spec = importlib.util.spec_from_file_location("next_due_churn", ROOT / "checks" / "next-due-churn.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_kept_timer_that_fires_is_not_churn():
    check = load_check()
    journal = "\n".join([
        "Starting mishe-next-due-aaaa11111111.timer...",
        "Started mishe-next-due-aaaa11111111.timer - mishe-next-due-aaaa11111111.timer.",
        "Starting mishe-next-due-aaaa11111111.service - due...",
        "Finished mishe-next-due-aaaa11111111.service - due.",
    ])
    timers, services = check.units_started(journal)
    assert timers == {"aaaa11111111"}
    assert check.evaluate(timers, services, window_s=600)[0] == 0


def test_replacements_without_a_fire_are_red():
    # Measured shape at release 4f0962ae: a start every few seconds, one due handler run.
    check = load_check()
    names = [f"{i:012x}" for i in range(1, 16)]
    journal = "\n".join(
        [f"Started mishe-next-due-{name}.timer - mishe-next-due-{name}.timer." for name in names]
        + ["Finished mishe-next-due-000000000001.service - due."])
    timers, services = check.units_started(journal)
    code, lines = check.evaluate(timers, services, window_s=600)
    assert code == 2, lines
    assert "14 replaced without a run" in lines[0]


def test_the_threshold_decides_between_kept_and_replaced():
    check = load_check()
    timers = {f"{i:012x}" for i in range(1, 12)}  # 11 starts
    services = {f"{i:012x}" for i in range(1, 2)}  # one fired, 10 replaced
    assert check.evaluate(timers, services, window_s=600, max_churn=10)[0] == 0
    assert check.evaluate(timers, services, window_s=600, max_churn=9)[0] == 2


def test_an_empty_window_is_undecidable_not_green():
    check = load_check()
    code, lines = check.evaluate(set(), set(), window_s=600)
    assert code == 3
    assert "next-due-armed.py owns" in lines[0]


def test_service_lines_only_match_this_unit_family():
    check = load_check()
    timers, services = check.units_started(
        "Started mishe-next-due-aaaa11111111.timer - t.\n"
        "Started mishe-producer-next-due-armed.service - Mishe producer next-due-armed.\n")
    assert timers == {"aaaa11111111"}
    assert services == set()

def test_a_neighbour_plants_timer_is_not_our_churn():
    check = load_check()
    journal = "\n".join([
        "Started mishe-next-due-aaaa11111111.timer - python -m mishe_tauftauf --home /site/us due.",
        "Started mishe-next-due-bbbb22222222.timer - python -m mishe_tauftauf --home /site/them due.",
    ])
    timers, services = check.units_started(journal, home="/site/us")
    assert timers == {"aaaa11111111"}
    assert check.evaluate(timers, services, window_s=600)[0] == 0


def test_plant_tagged_timer_names_are_paired_by_arm_hex():
    check = load_check()
    journal = "\n".join([
        "Started mishe-next-due-deadbeef-aaaa11111111.timer - python --home /site/us due.",
        "Finished mishe-next-due-deadbeef-aaaa11111111.service - python --home /site/us due.",
    ])
    timers, services = check.units_started(journal, home="/site/us")
    assert timers == services == {"aaaa11111111"}
    assert check.evaluate(timers, services, window_s=600)[0] == 0


def test_without_home_the_journal_is_attributed_in_full():
    check = load_check()
    journal = ("Started mishe-next-due-aaaa11111111.timer - python --home /site/us due.\n"
               "Started mishe-next-due-bbbb22222222.timer - python --home /site/them due.")
    timers, _ = check.units_started(journal)
    assert timers == {"aaaa11111111", "bbbb22222222"}


def test_a_bare_run_is_unknown_not_a_cross_plant_red(monkeypatch):
    check = load_check()
    monkeypatch.setattr(check, "read_journal", lambda _w:
                        "Started mishe-next-due-aaaa11111111.timer - python --home /site/them due.")
    assert check.main([]) == 3
