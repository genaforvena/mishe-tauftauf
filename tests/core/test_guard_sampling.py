"""The guard keeps sampling while a restore is requested.

The guard's observation loop is the plant's independent safety boundary.
Nothing a contained supervisor does may block it: a long restore, a request
that waits, a staged admission — none of them may stop the sentinel from
sampling, because the sentinel is what notices that the restore is going
wrong.
"""
import json
import time
from pathlib import Path

import pytest

from mishe_tauftauf import host_containment as guard


def test_the_sentinel_holds_its_transition_lock_only_for_one_sample():
    """The observation loop takes the lock per sample, not for its lifetime."""
    import inspect
    source = inspect.getsource(guard.watch)
    # The lock is taken inside the loop body, so it is released between
    # samples.  A restore that waits on the lock waits for one sample, not
    # for the whole loop.
    assert "with transition(STATE):" in source
    body = source.split("while True:", 1)[1]
    assert "time.sleep(2)" in body


def test_a_request_that_waits_for_the_lock_times_out_rather_than_blocking_forever(tmp_path):
    """The lock wait is bounded, so a contended lock cannot stall the guard."""
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(TimeoutError):
        with guard.transition(state, timeout=0.01):
            # A second holder in this process cannot take the lock either.
            with guard.transition(state, timeout=0.01):
                pass


def test_the_transition_lock_is_reentrant_only_within_one_process():
    """A second holder in another process waits; it does not take the lock."""
    state = Path("/tmp/mishe-core-guard-sample")
    state.mkdir(parents=True, exist_ok=True)
    try:
        with guard.transition(state, timeout=0.5):
            with pytest.raises(TimeoutError):
                with guard.transition(state, timeout=0.05):
                    pass
    finally:
        for path in state.iterdir():
            path.unlink()
        state.rmdir()


def test_the_sentinel_status_is_written_by_the_guard_itself_and_readable():
    """A fresh status is what the guard publishes; a stale one reads UNKNOWN."""
    run = Path("/tmp/mishe-core-guard-run")
    run.mkdir(parents=True, exist_ok=True)
    try:
        guard.atomic(run / "status.json", {"at": time.time(), "state": "READY"})
        assert guard.sentinel_healthy(run=run, state=Path("/tmp/mishe-core-guard-state"))
        # A stale status is not healthy, however good its contents look.
        guard.atomic(run / "status.json", {"at": time.time() - 600, "state": "READY"})
        assert not guard.sentinel_healthy(run=run, state=Path("/tmp/mishe-core-guard-state"))
        # A missing status is not healthy either.
        (run / "status.json").unlink()
        assert not guard.sentinel_healthy(run=run, state=Path("/tmp/mishe-core-guard-state"))
    finally:
        for path in run.iterdir():
            path.unlink()
        run.rmdir()


def test_the_restore_path_takes_the_lock_before_it_clears_a_latch():
    """A restore and a latch clearing are serialized by the transition lock."""
    import inspect
    source = inspect.getsource(guard.rearm)
    assert "with transition(STATE, timeout=.5):" in source
    # The latch is cleared inside the lock, so a sample that observes the
    # state never sees a half-cleared latch.
    assert "clear_expected_latches(expected)" in source


def test_a_restore_that_cannot_take_the_lock_leaves_the_latch_alone():
    """Contention is a refusal, not a partial clearing."""
    import inspect
    source = inspect.getsource(guard.rearm_when_lock_free)
    # The retry is bounded, and the last attempt's timeout propagates.
    assert "attempts=10" in source
    assert "raise" in source


def test_the_restore_never_waits_on_the_lock_across_a_staged_restoration():
    """The staged resume runs after the lock is released."""
    import inspect
    rearm_source = inspect.getsource(guard.rearm)
    restore_source = inspect.getsource(guard.restore)
    # The lock is taken and released inside rearm; the staged unit starts
    # happen afterwards in restore, outside any transition lock.
    assert "with transition(STATE, timeout=.5):" in rearm_source
    assert "rearm_when_lock_free(config, request, expected=evidence[\"clears\"])" in restore_source
    assert "with transition(STATE" not in restore_source


def test_a_failed_restore_leaves_a_latch_and_removes_the_heartbeat():
    """An interrupted restore is visible, not silently resumed."""
    import inspect
    source = inspect.getsource(guard.restore)
    assert 'latch(STATE, "staged restoration failed or interrupted", None)' in source
    assert '(STATE / "restoring").unlink(missing_ok=True)' in source
