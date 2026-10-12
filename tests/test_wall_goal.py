"""Focused fixtures for the isolated wall-goal retention reader.

Covers the operator acceptance fixture (chat 21232) at reader level only:
positive retained, controlled missing, mismatch/duplicate, recovery, and
unknown on malformed inputs. No discovery wiring or activation.
"""
from mishe_tauftauf import wall_goal

CHARTER = (
    "# senses\n"
    "\n"
    "Goal: turn useful readings into honest, visible senses.\n"
    "\n"
    "Body prose.\n"
)


def test_positive_exact_goal_with_different_next_is_retained():
    wall = CHARTER + "\nNext: probe the UVC lane.\n"
    row = wall_goal.assess_goal(CHARTER, wall)
    assert row["verdict"] == "retained"
    assert row["reason"] == "exact-match"
    assert row["charter_digest"] != row["wall_digest"]


def test_controlled_failure_missing_goal_reports_missing():
    wall = "# senses\n\nBody prose.\n\nNext: probe the UVC lane.\n"
    row = wall_goal.assess_goal(CHARTER, wall)
    assert row["verdict"] == "missing"
    assert row["reason"] == "wall-goal-absent"


def test_controlled_failure_different_goal_reports_mismatch():
    wall = "Goal: some other mission.\n\nNext: probe the UVC lane.\n"
    row = wall_goal.assess_goal(CHARTER, wall)
    assert row["verdict"] == "mismatch"
    assert row["reason"] == "value-differs"


def test_controlled_failure_duplicate_goal_reports_mismatch():
    wall = CHARTER + "Goal: turn useful readings into honest, visible senses.\n"
    row = wall_goal.assess_goal(CHARTER, wall)
    assert row["verdict"] == "mismatch"
    assert row["reason"] == "duplicate-wall-goal"


def test_recovery_restored_field_is_retained():
    missing_wall = "# senses\n\nNext: probe the UVC lane.\n"
    assert wall_goal.assess_goal(CHARTER, missing_wall)["verdict"] == "missing"
    assert wall_goal.assess_goal(CHARTER, CHARTER)["verdict"] == "retained"


def test_paraphrase_never_matches():
    wall = "Goal: turn useful readings into honest visible senses\n"
    row = wall_goal.assess_goal(CHARTER, wall)
    assert row["verdict"] == "mismatch"


def test_indented_goal_line_is_not_a_field():
    wall = "  Goal: turn useful readings into honest, visible senses.\n"
    row = wall_goal.assess_goal(CHARTER, wall)
    assert row["verdict"] == "missing"


def test_unknown_on_missing_or_malformed_inputs():
    assert wall_goal.assess_goal("", CHARTER)["verdict"] == "unknown"
    assert wall_goal.assess_goal(None, CHARTER)["verdict"] == "unknown"
    assert wall_goal.assess_goal(CHARTER, None)["verdict"] == "unknown"
    assert wall_goal.assess_goal(CHARTER, "no field\n")["verdict"] == "missing"
    ambiguous = CHARTER + "Goal: a second goal.\n"
    assert wall_goal.assess_goal(ambiguous, CHARTER)["verdict"] == "unknown"
    assert wall_goal.assess_goal("x" * 70000, CHARTER)["verdict"] == "unknown"


def test_assess_roles_and_summarize():
    other_charter = "Goal: keep the plant working.\n"
    rows = wall_goal.assess_roles({
        "senses": (CHARTER, CHARTER),
        "health": (other_charter, "no field\n"),
        "docs": ("no goal here\n", "Goal: anything\n"),
    })
    assert [r["role"] for r in rows] == ["docs", "health", "senses"]
    counts = wall_goal.summarize(rows)
    assert counts == {"retained": 1, "missing": 1, "mismatch": 0, "unknown": 1}
