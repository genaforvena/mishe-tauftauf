"""One-shot wakes with a fake engine: owed gating, crash safety, one at a time."""
import os
from pathlib import Path

import pytest

from mishe_tauftauf import arm, intentions, owed, signal_store as store, wake, yielding
from mishe_tauftauf.core.state import DispatchStore

ROLES = ["genome", "health", "docs"]
NOW = 1000.0


class FakeEngine:
    """Calls ``script(prompt, env)`` in place of a model; returns its exit code."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None):
        prompt = Path(prompt_file).read_text()
        self.calls.append((env["MISHE_ROLE"], prompt))
        return self.script(prompt, env)


def do_yield(prompt, env, **kw):
    yielding.settle(Path(env["MISHE_SEED_HOME"]), env["MISHE_ROLE"], env["MISHE_WAKE_KEY"],
                    text="wall v2", result="changed", now=NOW, wall_writer=lambda *a: None, **kw)
    return 0


@pytest.fixture
def home(tmp_path):
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "genome.md").write_text("MY WALL TEXT")
    store.register(tmp_path, "ci", max_age=60, subscribers=["genome"])
    store.register(tmp_path, "budget", max_age=600, subscribers=[])
    return tmp_path


def go(home, role, engine, started=None, **kw):
    return wake.run_wake(home, role, roles=ROLES, engine=engine, now=NOW,
                         mail_reader=lambda *a: [], starter=(started if started is not None else []).append, **kw)


def test_spurious_trigger_makes_no_model_call_and_leaves_no_trace(home):
    engine = FakeEngine(do_yield)
    result = go(home, "genome", engine)
    assert result.outcome == "skipped" and engine.calls == []
    assert DispatchStore(home / "dispatch").read("genome") is None


def test_owed_wake_runs_engine_with_dossier_and_yield_completes(home):
    owed.touch_inbox(home, "genome")
    engine = FakeEngine(do_yield)
    result = go(home, "genome", engine)
    assert result.outcome == "completed"
    prompt = engine.calls[0][1]
    assert "MY WALL TEXT" in prompt and "inbox" in prompt
    assert DispatchStore(home / "dispatch").read("genome").state.name == "completed"
    assert owed.check(home, "genome", now=NOW).reasons == []          # cursor advanced
    assert [i["id"] for i in intentions.list_(home, "genome")] == ["idle-initiative"]


def test_no_yield_leaves_dispatch_unknown_and_cursor_unmoved(home):
    owed.touch_inbox(home, "genome")
    result = go(home, "genome", FakeEngine(lambda p, e: 0))
    assert result.outcome == "unknown"
    assert DispatchStore(home / "dispatch").read("genome").state.name == "unknown"
    assert owed.check(home, "genome", now=NOW).reasons == ["inbox"]


def test_crash_mid_wake_is_unknown_and_next_wake_reconciles_first(home):
    owed.touch_inbox(home, "genome")
    def crash(p, e):
        raise RuntimeError("engine died")
    with pytest.raises(RuntimeError):
        go(home, "genome", FakeEngine(crash))
    assert DispatchStore(home / "dispatch").read("genome").state.name == "unknown"
    engine = FakeEngine(do_yield)
    result = go(home, "genome", engine)
    assert result.outcome == "reconciled"
    assert "RECONCILE FIRST" in engine.calls[0][1] and "worker raised" in engine.calls[0][1]
    assert DispatchStore(home / "dispatch").read("genome").state.name == "reconciled"
    assert owed.check(home, "genome", now=NOW).reasons == []


def test_unknown_effect_is_never_replayed_by_a_plain_dispatch(home):
    owed.touch_inbox(home, "genome")
    go(home, "genome", FakeEngine(lambda p, e: 0))          # leaves unknown
    keys = DispatchStore(home / "dispatch").used_keys("genome")
    go(home, "genome", FakeEngine(lambda p, e: 0))          # reconcile wake, no yield
    store_ = DispatchStore(home / "dispatch")
    assert store_.read("genome").state.name == "unknown"
    assert store_.read("genome").key in keys                 # same work, never a fresh claim


def test_command_that_never_started_is_a_plain_failure(home):
    owed.touch_inbox(home, "genome")
    def missing(p, e):
        raise wake.EngineNotStarted("no such binary")
    result = go(home, "genome", FakeEngine(missing))
    assert result.outcome == "failed"
    assert DispatchStore(home / "dispatch").read("genome").state.name == "failed"
    assert owed.check(home, "genome", now=NOW).reasons == ["inbox"]


def test_other_roles_open_dispatch_blocks_and_is_offered_first(home):
    owed.touch_inbox(home, "genome")
    go(home, "genome", FakeEngine(lambda p, e: 0))          # genome unknown
    owed.touch_inbox(home, "health")
    started = []
    engine = FakeEngine(do_yield)
    result = go(home, "health", engine, started)
    assert result.outcome == "blocked" and engine.calls == []
    assert started == ["genome"]


def test_one_wake_at_a_time_and_next_owed_role_starts_on_exit(home):
    owed.touch_inbox(home, "genome")
    owed.touch_inbox(home, "docs")
    started, inner = [], []
    def script(prompt, env):
        inner.append(go(home, "docs", FakeEngine(do_yield)))   # while genome holds the lock
        return do_yield(prompt, env)
    result = go(home, "genome", FakeEngine(script), started)
    assert inner[0].outcome == "busy"
    assert result.outcome == "completed" and started == ["docs"]


def test_hold_file_stops_every_wake(home):
    owed.touch_inbox(home, "genome")
    (home / "hold").write_text("held by owner")
    engine = FakeEngine(do_yield)
    result = go(home, "genome", engine)
    assert result.outcome == "held" and engine.calls == []


def test_yield_again_and_when_write_intentions(home):
    owed.touch_inbox(home, "genome")
    def script(p, e):
        return do_yield(p, e, again=3600, when={"signal": "ci", "equals": "RED"}, continue_=True)
    go(home, "genome", FakeEngine(script))
    ids = sorted(i["id"] for i in intentions.list_(home, "genome"))
    assert ids == ["again", "continue", "idle-initiative", "when-ci"]


def test_yield_twice_is_idempotent(home):
    owed.touch_inbox(home, "genome")
    def twice(p, e):
        do_yield(p, e)
        return do_yield(p, e)
    assert go(home, "genome", FakeEngine(twice)).outcome == "completed"


def test_arm_targets_earliest_of_intention_due_and_signal_expiry(home):
    intentions.add(home, "genome", "x", note="n", due=5000.0)
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")        # expires 1060
    seen = []
    assert arm.arm(home, scheduler=seen.append) == 1060.0 and seen == [1060.0]
    store.expire(home, now=1100.0)
    arm.arm(home, scheduler=seen.append)
    assert seen[-1] == 5000.0


def test_arm_with_nothing_to_wait_for_clears_the_timer(home):
    seen = []
    assert arm.arm(home, scheduler=seen.append) is None and seen == [None]


def test_due_expires_signals_then_rearms(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    seen = []
    changed = arm.due(home, now=1100.0, scheduler=seen.append)
    assert changed == ["ci"] and seen == [None]


def test_engine_pid_is_published_to_the_open_record_while_it_runs(home):
    owed.touch_inbox(home, "genome")
    seen = []
    class PidEngine:
        def run(self, prompt_file, *, env, cwd, timeout, on_start=None):
            on_start(4242)
            seen.append(DispatchStore(home / "dispatch").read("genome").pid)
            return do_yield("", env)
    go(home, "genome", PidEngine())
    assert seen == [4242]


def test_live_worker_blocks_reconcile(home):
    owed.touch_inbox(home, "genome")
    go(home, "genome", FakeEngine(lambda p, e: 0))                      # unknown
    store_ = DispatchStore(home / "dispatch")
    rec = store_.read("genome")
    from mishe_tauftauf.core.state import DispatchRecord
    store_.write(DispatchRecord(role="genome", key=rec.key, state=rec.state, detail=rec.detail,
                                pid=os.getpid(), attempt=rec.attempt))   # a live pid
    engine = FakeEngine(do_yield)
    assert go(home, "genome", engine).outcome == "blocked" and engine.calls == []


def test_due_offers_the_first_owed_role_only(home):
    owed.touch_inbox(home, "health")
    owed.touch_inbox(home, "docs")
    started = []
    arm.due(home, now=NOW, scheduler=lambda t: None, roles=ROLES, starter=started.append)
    assert started == ["health"]          # roles are checked in the order given; the wake chain does the rest
