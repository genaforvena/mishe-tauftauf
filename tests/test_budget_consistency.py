"""The budget-consistency detector judges what the producer actually writes.

The producer carries the engine's own window labels (FRESH, STALE, UNKNOWN, UNATTRIBUTED,
UNREADABLE). The detector must accept every one of them as a legitimate, non-admitting state and
judge only FRESH against the age and reset rules.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mishe_tauftauf import budget_producer as bp  # noqa: E402
from mishe_tauftauf import yielding  # noqa: E402

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
    (tmp_path / "engine").mkdir(parents=True, exist_ok=True)
    (tmp_path / "engine" / "budget.json").write_text(json.dumps(budget))
    return tmp_path


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
