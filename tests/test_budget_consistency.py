"""The budget-consistency detector judges what the producer actually writes.

The producer carries the engine's own window labels (FRESH, STALE, UNKNOWN, UNATTRIBUTED,
UNREADABLE). The detector must accept every one of them as a legitimate, non-admitting state and
judge only FRESH against the age and reset rules.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mishe_tauftauf import budget_producer as bp  # noqa: E402
from mishe_tauftauf import owed, producers, signal_store, yielding  # noqa: E402

_spec = importlib.util.spec_from_file_location("budget_consistency", REPO / "checks" / "budget-consistency.py")
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)

NOW = 1794400000.0


def _status(window_state: str, account_state: str = "FRESH", observed_at: float = NOW - 243) -> dict:
    return {
        "accounts": {
            "codex-a": {
                "provider": "codex",
                "state": account_state,
                "observed_at": observed_at,
                "windows": {
                    "5h": {"remaining": 0.5, "reset_at": NOW + 3600, "state": window_state},
                },
            }
        }
    }


def _home_with(tmp_path: Path, budget: dict) -> Path:
    """A home with the budget file and a pin (runtime/current/src) to the checkout src."""
    (tmp_path / "engine").mkdir(parents=True, exist_ok=True)
    (tmp_path / "engine" / "budget.json").write_text(json.dumps(budget))
    _pin(tmp_path, REPO / "src")
    return tmp_path


def _pin(home: Path, src: Path) -> None:
    release = home / "runtime" / "current"
    release.mkdir(parents=True, exist_ok=True)
    (release / "src").symlink_to(src, target_is_directory=True)


@pytest.mark.parametrize("label", ["FRESH", "STALE", "UNKNOWN", "UNATTRIBUTED", "UNREADABLE"])
def test_producer_output_with_each_window_label_is_accepted(tmp_path: Path, label: str):
    budget = bp.build_budget(_status(label), None, NOW)
    code, result = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert result["problems"] == []
    assert code == bc.GREEN


def test_engine_unknown_window_is_not_malformed_and_routes_no_admission(tmp_path: Path):
    budget = bp.build_budget(_status("UNKNOWN"), None, NOW)
    assert budget["accounts"]["codex-a"]["windows"]["5h"]["state"] == "UNKNOWN"
    assert budget["accounts"]["codex-a"]["state"] == "STALE"
    code, result = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert code == bc.GREEN
    assert result["accounts"] == {"codex-a": "STALE", "claude": "UNREADABLE"}


def test_unrecognised_window_label_is_still_red(tmp_path: Path):
    budget = bp.build_budget(_status("FRESH"), None, NOW)
    budget["accounts"]["codex-a"]["windows"]["5h"]["state"] = "BOGUS"
    code, result = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert code == bc.RED
    assert any("BOGUS" in p for p in result["problems"])


def test_window_shown_fresh_past_max_age_is_red(tmp_path: Path):
    budget = bp.build_budget(_status("FRESH", observed_at=NOW - 1000), None, NOW)
    # the producer itself would never write this; a hand-made file must still be caught
    budget["accounts"]["codex-a"]["windows"]["5h"]["state"] = "FRESH"
    budget["accounts"]["codex-a"]["state"] = "FRESH"
    code, result = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert code == bc.RED
    assert any("shown FRESH" in p for p in result["problems"])


def test_window_whose_reset_passes_after_production_is_not_red(tmp_path: Path):
    # The producer labelled the window FRESH at produced_at; the reset passes while the file ages
    # (producer period 600 s inside max_age 900 s, so this happens at every window boundary). Every
    # consumer re-ages the window itself, so a correctly written snapshot is not a producer fault.
    window = {"used": 0.42, "remaining": 0.58, "reset_at": NOW - 10, "observed_at": NOW - 25, "state": "FRESH"}
    budget = {"schema": 1, "produced_at": NOW - 20,
              "accounts": {"codex-a": {"state": "FRESH", "windows": {"5h": dict(window), "7d": dict(window)}}}}
    code, result = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert code == bc.GREEN, result["problems"]


def test_window_fresh_with_reset_already_past_at_production_is_red(tmp_path: Path):
    # A producer (or hand-edited file) that writes FRESH for a window whose reset had already passed
    # at produced_at is a real fault, and stays RED.
    window = {"used": 0.42, "remaining": 0.58, "reset_at": NOW - 60, "observed_at": NOW - 5, "state": "FRESH"}
    budget = {"schema": 1, "produced_at": NOW,
              "accounts": {"codex-a": {"state": "FRESH", "windows": {"5h": dict(window)}}}}
    home = _home_with(tmp_path, budget)
    code, result = bc.run(home, 900.0, NOW, home / "engine" / "budget.json")
    assert code == bc.RED
    assert any("shown FRESH" in p for p in result["problems"])


def test_account_with_malformed_window_beside_fresh_one_is_unreadable_not_red(tmp_path: Path):
    # the producer's own output: a valid FRESH window plus one the producer cannot read a number from
    status = _status("FRESH")
    status["accounts"]["codex-a"]["windows"]["7d"] = {"remaining": "??", "reset_at": NOW + 3600, "state": "FRESH"}
    budget = bp.build_budget(status, None, NOW)
    assert budget["accounts"]["codex-a"]["state"] == "UNREADABLE"
    code, result = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert not any("its windows say" in p for p in result["problems"]), result["problems"]
    assert result["accounts"]["codex-a"] == "UNREADABLE"


def test_account_fresh_over_a_non_fresh_window_is_red(tmp_path: Path):
    budget = bp.build_budget(_status("UNKNOWN"), None, NOW)
    budget["accounts"]["codex-a"]["state"] = "FRESH"
    code, result = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert code == bc.RED
    assert any("its windows say STALE" in p for p in result["problems"])


def test_missing_budget_file_is_unknown_not_green(tmp_path: Path):
    code, result = bc.run(tmp_path, 900.0, NOW, None)
    assert code == bc.UNKNOWN


def test_old_produced_at_is_unknown_not_green(tmp_path: Path):
    budget = bp.build_budget(_status("FRESH"), None, NOW - 5000)
    code, _ = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert code == bc.UNKNOWN


def test_detector_enum_matches_the_producer_states():
    assert bc.WINDOW_STATES == {"FRESH", "STALE"} | set(bp.SOURCE_STATES)


def _admit_budget(age: float, state: str = "FRESH") -> dict:
    window = {"used": 0.5, "remaining": 0.5, "reset_at": NOW + 3600, "observed_at": NOW - age, "state": state}
    return {
        "schema": 1,
        "produced_at": NOW,
        "accounts": {
            "codex-a": {"state": state, "windows": {"5h": dict(window), "7d": dict(window)}},
        },
    }


@pytest.mark.parametrize("age,expected", [(800.0, True), (1200.0, False), (3000.0, False)])
def test_admission_bounds_match_the_scheduler_for_hand_made_fresh_windows(tmp_path: Path, age: float, expected: bool):
    # 901..3600 s: the scheduler's 3600 s evidence limit allows it, but the producer's 900 s rule does not (DM 300163)
    budget = _admit_budget(age)
    home = _home_with(tmp_path, budget)
    assert bc.raw_admission(budget, NOW) is expected
    assert yielding.spare_budget(home, NOW) is expected


def test_producer_shaped_stale_window_past_900s_agrees_with_no_admission(tmp_path: Path):
    budget = _admit_budget(1200.0, state="STALE")
    code, result = bc.run(_home_with(tmp_path, budget), 900.0, NOW, None)
    assert code == bc.GREEN, result
    assert result["admission"]["decided"] is False
    assert result["admission"]["raw"] is False


def test_missing_pin_is_unknown_not_green_from_the_checkout(tmp_path: Path):
    # no runtime/current: the scheduler decision is not observed, so the checkout src must not stand in for it
    budget = _admit_budget(800.0)
    (tmp_path / "engine").mkdir(parents=True)
    (tmp_path / "engine" / "budget.json").write_text(json.dumps(budget))
    code, result = bc.run(tmp_path, 900.0, NOW, None)
    assert code == bc.UNKNOWN, result
    assert result["admission"]["available"] is False
    assert "no pinned scheduler" in result["reason"]


def test_broken_pinned_scheduler_is_unknown(tmp_path: Path):
    broken = tmp_path / "pin-src"
    (broken / "mishe_tauftauf").mkdir(parents=True)
    (broken / "mishe_tauftauf" / "__init__.py").write_text("")
    (broken / "mishe_tauftauf" / "yielding.py").write_text("raise RuntimeError('broken pin')\n")
    home = tmp_path / "home"
    budget = _admit_budget(800.0)
    (home / "engine").mkdir(parents=True)
    (home / "engine" / "budget.json").write_text(json.dumps(budget))
    _pin(home, broken)
    code, result = bc.run(home, 900.0, NOW, None)
    assert code == bc.UNKNOWN, result
    assert "not importable or raised" in result["reason"]


def test_regressed_pinned_admission_rule_is_red_through_the_detector(tmp_path: Path):
    # controlled decision regression: the pinned copy admits any window (drops the freshness bound)
    pin = tmp_path / "pin-src"
    shutil.copytree(REPO / "src", pin)
    yl = pin / "mishe_tauftauf" / "yielding.py"
    text = yl.read_text()
    needle = 'budget_producer.window_fresh_at(x.get("observed_at"), x.get("reset_at"), now)'
    assert needle in text
    yl.write_text(text.replace(needle, "True"))
    home = tmp_path / "home"
    budget = _admit_budget(1200.0)
    (home / "engine").mkdir(parents=True)
    (home / "engine" / "budget.json").write_text(json.dumps(budget))
    _pin(home, pin)
    code, result = bc.run(home, 900.0, NOW, None)
    assert code == bc.RED, result
    assert result["admission"]["decided"] is True and result["admission"]["raw"] is False
    assert result["admission"]["scheduler_file"].startswith(str(pin.resolve()))


def test_detector_judges_the_pinned_bytes_not_the_checkout(tmp_path: Path):
    budget = _admit_budget(800.0)
    home = _home_with(tmp_path, budget)
    code, result = bc.run(home, 900.0, NOW, None)
    assert code == bc.GREEN, result
    assert result["admission"]["scheduler_file"].startswith(str((REPO / "src").resolve()))
    assert result["admission"]["idle_routing"] == bc.IDLE_ROUTING_EXPECTED


def test_regressed_idle_fallback_publishes_red_and_wakes_steward(tmp_path, monkeypatch):
    pin = tmp_path / "pin-src"
    shutil.copytree(REPO / "src", pin)
    router = pin / "mishe_tauftauf" / "engine_router.py"
    source = router.read_text()
    gate = "free_only = spontaneous_only and not owed._budget_green(self.home, now, self.role)"
    assert gate in source
    router.write_text(source.replace(gate, "free_only = False", 1))
    home = tmp_path / "home"
    budget = _admit_budget(800.0)
    (home / "engine").mkdir(parents=True)
    (home / "engine" / "budget.json").write_text(json.dumps(budget))
    _pin(home, pin)
    name = "budget-consistency"
    signal_store.register(home, name, max_age=900, subscribers=["steward"], now=NOW - 60)
    signal_store.observe(home, name, "GREEN", now=NOW - 60, source="test")
    owed.advance(home, "steward", owed.check(home, "steward", now=NOW - 60), now=NOW - 60)
    monkeypatch.setattr(producers, "_restore_memory_floor", lambda *_: {"unit": None})
    check = [sys.executable, "-B", bc.__file__, "--home", str(home), "--now", str(NOW)]

    assert producers.run(home, name, check, now=NOW, timeout=60, red_codes=(2,)) == 0
    assert signal_store.read(home, name, now=NOW).value == "RED"
    assert f"signal:{name}" in owed.check(home, "steward", now=NOW).reasons
    detail = json.loads((home / "signals" / f"{name}.detail").read_text())
    assert detail["admission"]["idle_routing"]["free-refuses"] == [["free", "paid"], "completed"]
