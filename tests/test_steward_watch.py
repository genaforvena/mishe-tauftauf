"""Steward watch: per-mind checks the steward used to run by hand, as pure code."""
from mishe_tauftauf.clear_journal import wake_held
from mishe_tauftauf.omp_pane import capture_tail, pane_is_busy
from mishe_tauftauf.steward_watch import WALL_STALE_S, classify_mind

HELD = ["Nov 03 12:31:50 python3[1]: held seed docs wake 1 delivery retry exhausted"]
CLEARED = HELD + ["Nov 03 12:40:00 python3[1]: clear seed docs after 1"]
IDLE = ["> INSERT > GPT-6 Luna"]
IDLE_BLOCKED = ["☐ Settle wake (blocked)", "> INSERT > GPT-6 Luna"]
BUSY = ["⠙ 2m > INSERT > ⬢ GPT-6 Luna · ◒ high"]
FRESH = 60
STALE = WALL_STALE_S + 1


def test_missing_pane_is_dead_and_routes_to_health():
    result = classify_mind("docs", pane_tail=None, journal_lines=[], wall_age_s=FRESH)
    assert result["pane"] == "DEAD"
    assert result["action"] == "alert-health"


def test_empty_pane_capture_is_dead_not_idle():
    result = classify_mind("docs", pane_tail=[], journal_lines=[], wall_age_s=FRESH)
    assert result["pane"] == "DEAD"


def test_missing_wall_fails_closed_as_stale():
    result = classify_mind("docs", pane_tail=IDLE, journal_lines=[], wall_age_s=None)
    assert result["wall"] == "STALE"
    assert result["action"] == "resend-wake"


def test_held_wake_routes_to_resolver_even_with_fresh_wall():
    result = classify_mind("docs", pane_tail=IDLE, journal_lines=HELD, wall_age_s=FRESH)
    assert result["wake"] == "HELD"
    assert result["action"] == "dm-held-wake"


def test_cleared_wake_is_not_held():
    assert not wake_held(CLEARED)
    assert wake_held(HELD)


def test_idle_and_stale_wall_resends_wake():
    result = classify_mind("docs", pane_tail=IDLE, journal_lines=[], wall_age_s=STALE)
    assert result["idle"] == "IDLE"
    assert result["action"] == "resend-wake"


def test_idle_with_fresh_wall_and_no_hold_is_ok():
    result = classify_mind("docs", pane_tail=IDLE, journal_lines=[], wall_age_s=FRESH)
    assert result["action"] == "ok"


def test_blocked_plan_line_is_idle_mid_turn_not_ok():
    result = classify_mind("docs", pane_tail=IDLE_BLOCKED, journal_lines=[], wall_age_s=FRESH)
    assert result["idle"] == "IDLE_MID_TURN"
    assert result["action"] == "dm-held-wake"


def test_busy_with_stale_wall_is_flagged_not_resent():
    result = classify_mind("docs", pane_tail=BUSY, journal_lines=[], wall_age_s=STALE)
    assert result["idle"] == "BUSY"
    assert result["action"] == "check-busy-stale"


def test_busy_with_fresh_wall_is_ok():
    result = classify_mind("docs", pane_tail=BUSY, journal_lines=[], wall_age_s=FRESH)
    assert result["action"] == "ok"


def test_capture_tail_keeps_last_nonblank_lines():
    assert capture_tail("a\n\n b \n\nc\n", 2) == [" b ", "c"]


def test_claude_interrupt_hint_is_busy_and_plain_prompt_is_not():
    assert pane_is_busy(["✻ Thinking… (esc to interrupt)"])
    assert not pane_is_busy(["> INSERT > GPT-6 Luna"])
