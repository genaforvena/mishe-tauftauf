from mishe_tauftauf.mind_liveness import is_busy, plan_blocked, verdict, wake_held

HELD = ["Nov 03 12:31:50 python3[1]: held seed docs wake 1 delivery retry exhausted"]
CLEARED = HELD + ["Nov 03 12:40:00 python3[1]: clear seed docs after 1"]
IDLE_BLOCKED = ["☐ Settle wake (blocked)", "> INSERT > GPT-6 Luna"]
CODEX_BUSY = ["⠙ 2m > INSERT > ⬢ GPT-6 Luna · ◒ high"]
CLAUDE_BUSY = ["✻ Thinking… (esc to interrupt)"]


def test_held_wake_with_no_clear_is_held():
    assert wake_held(HELD)


def test_cleared_wake_is_not_held():
    assert not wake_held(CLEARED)


def test_spinner_and_interrupt_hint_are_busy():
    assert is_busy(CODEX_BUSY)
    assert is_busy(CLAUDE_BUSY)
    assert not is_busy(["> INSERT"])


def test_blocked_plan_line_is_detected():
    assert plan_blocked(IDLE_BLOCKED)
    assert not plan_blocked(["> INSERT"])


def test_idle_with_held_wake_is_idle_mid_turn_regardless_of_wall_age():
    assert verdict(IDLE_BLOCKED, HELD, 60) == "IDLE_MID_TURN"
    assert verdict(IDLE_BLOCKED, HELD, 7200) == "IDLE_MID_TURN"


def test_busy_with_fresh_wall_is_busy():
    assert verdict(CODEX_BUSY, HELD, 60) == "BUSY"


def test_busy_with_stale_wall_is_busy_stale():
    assert verdict(CODEX_BUSY, HELD, 7200) == "BUSY_STALE"


def test_idle_with_nothing_pending_is_idle():
    assert verdict(["> INSERT"], [], 9999) == "IDLE"
    assert verdict(["> INSERT"], CLEARED, 9999) == "IDLE"
