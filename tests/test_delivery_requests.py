"""Delivery runs after settlement, never under the engine parent lock."""
import os
import time
import subprocess
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import delivery_requests as q, owed, wake, yielding
from mishe_tauftauf import release_promotion as rp
from mishe_tauftauf.state_files import read_json, write_json
from mishe_tauftauf.core.state import DispatchStore
from mishe_tauftauf.core.interfaces import STARTED, UNKNOWN
from mishe_tauftauf.core.state import DispatchRecord


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def plant(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    home = repo / "site"; home.mkdir()
    (home / "top-pains").mkdir(); (home / "minds").mkdir(); (home / "chat.log").touch()
    (home / "charters").mkdir(); (home / "charters/docs.md").write_text("Docs")
    git(repo, "init", "-q"); git(repo, "config", "user.name", "test"); git(repo, "config", "user.email", "test@example.org")
    (repo / ".gitignore").write_text("/site/\n")
    (repo / "docs").mkdir(); (repo / "docs/page.md").write_text("old")
    git(repo, "add", "."); git(repo, "commit", "-qm", "old"); old = git(repo, "rev-parse", "HEAD")
    (repo / "docs/page.md").write_text("new")
    git(repo, "add", "."); git(repo, "commit", "-qm", "new"); sha = git(repo, "rev-parse", "HEAD")
    # The effect is isolated, but real promote checks, records and recovery run.
    monkeypatch.setattr(rp, "closure", lambda *a: None)
    monkeypatch.setattr(rp, "enqueue_review", lambda *a: None)
    monkeypatch.setattr(wake, "systemd_starter", lambda *a: None)
    return home, repo, old, sha


def request(plant, key="wake-docs-test"):
    home, repo, old, sha = plant
    write_json(yielding.snapshot_path(home, key), dict(role="docs", snapshot=owed.Snapshot().to_json()))
    return q.enqueue(home, repo, sha, previous=old, check=[sys.executable, "-c", "assert True"],
                     observe=[sys.executable, "-c", "assert True"], role="docs", key=key)


def settle(home, key):
    yielding.settle(home, "docs", key, text="Work", result="changed", now=100, wall_writer=lambda *a: None)


def test_request_is_idempotent_and_waits_for_yield(plant):
    first = request(plant); second = request(plant)
    assert first["id"] == second["id"]
    assert q.consume(plant[0]) == []
    assert read_json(q.location(plant[0], first["id"]))["state"] == "queued"


def test_real_wake_cannot_promote_inside_engine_but_delivers_after_settlement(plant, monkeypatch):
    home, repo, old, sha = plant; observed = []; started = []
    original = rp.promote
    def isolated(*args, **kw):
        kw.update(activate=lambda source, target: observed.append(target), closure=lambda *a: None)
        return original(*args, **kw)
    monkeypatch.setattr(rp, "promote", isolated)
    class Engine:
        def run(self, prompt, *, env, **kwargs):
            with pytest.raises(ValueError, match="dispatch lock"):
                rp.promote(home, repo, sha, previous=old, check=["true"], observe=["true"])
            q.enqueue(home, repo, sha, previous=old, check=[sys.executable, "-c", "assert True"],
                      observe=[sys.executable, "-c", "assert True"], role="docs", key=env["MISHE_WAKE_KEY"])
            settle(home, env["MISHE_WAKE_KEY"])
            assert observed == []
            return 0
    owed.touch_inbox(home, "docs")
    result = wake.run_wake(home, "docs", roles=["docs"], engine=Engine(), now=100, repo=repo,
                           mail_reader=lambda *a: [], starter=started.append)
    assert result.outcome == "completed"
    assert observed == [sha]
    rows = [read_json(p) for p in (home / "work/delivery-requests").glob("*.json")]
    assert rows[0]["state"] == "delivered"
    assert not q.admission_path(home).exists()


@pytest.mark.parametrize("state", [STARTED, UNKNOWN])
def test_open_dispatch_prevents_delivery(plant, state):
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    DispatchStore(home / "dispatch").write(DispatchRecord(role="docs", key="earlier", state=state))
    assert q.consume(home) == []
    assert not q.admission_path(home).exists()


def test_existing_hold_is_preserved(plant):
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    (home / "hold").write_text("human hold")
    assert q.consume(home) == []
    assert (home / "hold").read_text() == "human hold"


def test_interrupted_delivery_retains_admission_and_unknown(plant, monkeypatch):
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    def crash(*a, **kw):
        raise KeyboardInterrupt()
    monkeypatch.setattr(rp, "promote", crash)
    with pytest.raises(KeyboardInterrupt): q.consume(home)
    assert q.admission_path(home).exists()
    q.consume(home)
    assert read_json(q.location(home, row["id"]))["state"] == "UNKNOWN"
    assert q.admission_path(home).exists()


def test_foreign_delivery_cannot_enter_queue_admission(plant):
    home, *_ = plant
    write_json(q.admission_path(home), dict(id="request", token="secret"))
    with pytest.raises(ValueError, match="delivery admission"):
        with rp.delivery_lock(home): pass


def test_unknown_admission_blocks_engine_and_reconciliation(plant):
    home, repo, *_ = plant
    write_json(q.admission_path(home), dict(id="request", token="secret"))
    owed.touch_inbox(home, "docs")
    class Engine:
        def run(self, *a, **kw): pytest.fail("engine must not start")
    result = wake.run_wake(home, "docs", roles=["docs"], engine=Engine(), now=100, repo=repo)
    assert result.outcome == "held"


def test_consumer_resumes_owed_work_after_periodic_delivery(plant, monkeypatch):
    home, repo, old, sha = plant; row = request(plant); settle(home, row["key"])
    original = rp.promote; resumed = []
    def isolated(*a, **kw):
        return original(*a, **kw, activate=lambda *a: None, closure=lambda *a: None)
    monkeypatch.setattr(rp, "promote", isolated)
    monkeypatch.setattr(wake, "systemd_starter", resumed.append)
    assert q.consume(home)[0]["state"] == "delivered"
    assert resumed == ["docs"]  # outcome notification is new owed evidence


def test_failed_observation_uses_real_rollback_and_old_probe(plant, monkeypatch):
    home, repo, old, sha = plant
    key = "wake-docs-failing"
    write_json(yielding.snapshot_path(home, key), dict(role="docs", snapshot=owed.Snapshot().to_json()))
    row = q.enqueue(home, repo, sha, previous=old, role="docs", key=key,
                    check=[sys.executable, "-c", "assert True"],
                    observe=[sys.executable, "-c", "from pathlib import Path; assert Path('docs/page.md').read_text() == 'old'"])
    settle(home, key); pins = []; original = rp.promote
    def isolated(*a, **kw):
        return original(*a, **kw, activate=lambda source, sha: pins.append(sha), closure=lambda *a: None)
    monkeypatch.setattr(rp, "promote", isolated)
    result = q.consume(home)[0]
    assert result["state"] == "failed"
    assert pins == [sha, old]
    record = read_json(rp.record_path(home, result["release_id"]))
    assert record["phase"] == "rolled-back" and record["rollback_probe"]["code"] == 0
    assert not q.admission_path(home).exists()


def test_mutated_request_is_preserved_and_cannot_execute(plant):
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    path = q.location(home, row["id"])
    row["spec"]["observe"] = ["false"]; write_json(path, row)
    with pytest.raises(ValueError, match="malformed delivery request"):
        q.consume(home)
    assert read_json(path) == row
    assert not q.admission_path(home).exists()


def test_already_running_patch_prevents_admission(plant):
    import fcntl
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    with (home / ".wall-patch.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert q.consume(home) == []
    assert not q.admission_path(home).exists()


def test_waiting_worker_rechecks_admission_under_dispatch_lock(plant):
    from mishe_tauftauf.core.interfaces import Dispatch
    home, repo, *_ = plant
    class Engine:
        def run(self, *a, **kw): pytest.fail("waiting engine must not run")
    worker = wake._Worker(home, Engine(), 100, lambda *a: [], repo, 100)
    owed.touch_inbox(home, "docs")
    with rp.delivery_lock(home):
        write_json(q.admission_path(home), dict(id="request", token="other"))
        assert worker.run(Dispatch(role="docs", key="waiting", payload={})).outcome == "skipped"


def test_foreign_revert_refused_even_with_patch_lock_acquired(plant):
    from mishe_tauftauf import wall_patch
    home, *_ = plant
    write_json(q.admission_path(home), dict(id="request", token="other"))
    with pytest.raises(ValueError, match="delivery admission"):
        wall_patch.revert(home, "unrelated", ["true"])


def strong_request(plant, monkeypatch, *, bad_new=False, bad_old=False):
    from mishe_tauftauf import wall_patch
    home, repo, old, sha = plant
    runtime = home / "patch-runtime"; (runtime / "docs").mkdir(parents=True)
    (runtime / "docs/page.md").write_text("old")
    write_json(home / "coordination-mode.json", dict(runtime=str(runtime)))
    wall_patch.prepare(home, "strong", ["docs/page.md"])
    monkeypatch.setattr(wall_patch, "review", lambda *a: dict(clear=True, request_hash="independent-test-review"))
    def probe(expected, bad):
        return [sys.executable, "-c", f"from pathlib import Path; assert Path({str(runtime / 'docs/page.md')!r}).read_text() == {expected!r}; assert {not bad!r}"]
    wall_patch.check(home, "strong", [sys.executable, "-c", "assert True"],
                     activate=[sys.executable, "-c", "pass"], observe=probe("new", bad_new),
                     revert_observe=probe("old", bad_old))
    key = "wake-docs-strong"
    write_json(yielding.snapshot_path(home, key), dict(role="docs", snapshot=owed.Snapshot().to_json()))
    row = q.enqueue(home, repo, sha, previous=old, role="docs", key=key, patch="strong")
    settle(home, key)
    return row, runtime


def test_strong_request_exercises_real_apply_revert_reapply(plant, monkeypatch):
    from mishe_tauftauf import wall_patch
    home, *_ = plant; row, runtime = strong_request(plant, monkeypatch)
    assert q.consume(home)[0]["state"] == "delivered"
    assert wall_patch.verified_delivery(wall_patch.status(home, "strong"))
    assert (runtime / "docs/page.md").read_text() == "new"
    assert not q.admission_path(home).exists()


@pytest.mark.parametrize("bad_old", [False, True])
def test_strong_failed_new_probe_needs_observed_old_consumer(plant, monkeypatch, bad_old):
    home, *_ = plant; row, runtime = strong_request(plant, monkeypatch, bad_new=True, bad_old=bad_old)
    result = q.consume(home)[0]
    assert result["state"] == ("UNKNOWN" if bad_old else "failed")
    assert result["recovery_observation"]["code"] == int(bad_old)
    assert (runtime / "docs/page.md").read_text() == "old"
    assert q.admission_path(home).exists() == bad_old


@pytest.mark.parametrize("field", ["review", "delivery_plan"])
def test_strong_review_or_plan_drift_rejects_without_effect(plant, monkeypatch, field):
    from mishe_tauftauf import wall_patch
    home, *_ = plant; row, runtime = strong_request(plant, monkeypatch)
    record = wall_patch.status(home, "strong"); record[field]["changed"] = True
    write_json(wall_patch.location(home, "strong"), record)
    result = q.consume(home)[0]
    assert result["state"] == "failed" and "changed since request" in result["error"]
    assert (runtime / "docs/page.md").read_text() == "old"
    assert not q.admission_path(home).exists()


def test_strong_does_not_cover_unreviewed_committed_path(plant, monkeypatch):
    home, repo, old, sha = plant; row, runtime = strong_request(plant, monkeypatch)
    (repo / "docs/other.md").write_text("Unreviewed")
    git(repo, "add", "docs/other.md"); git(repo, "commit", "-qm", "extra")
    with pytest.raises(ValueError, match="every changed committed path"):
        q.enqueue(home, repo, git(repo, "rev-parse", "HEAD"), previous=old,
                  role="docs", key=row["key"], patch="strong")


def test_readme_only_request_is_eligible_without_a_reviewed_patch(plant):
    home, repo, old, sha = plant
    (repo / "README.md").write_text("reader front page")
    git(repo, "add", "README.md"); git(repo, "commit", "-qm", "readme")
    readme = git(repo, "rev-parse", "HEAD")
    key = "wake-docs-readme"
    write_json(yielding.snapshot_path(home, key), dict(role="docs", snapshot=owed.Snapshot().to_json()))
    row = q.enqueue(home, repo, readme, previous=old, role="docs", key=key,
                    check=[sys.executable, "-c", "assert True"],
                    observe=[sys.executable, "-c", "assert True"])
    assert row["state"] == "queued"
    assert row["spec"]["patch"] is None


def test_mixed_readme_and_scheduler_request_is_refused_without_reviewed_strong_evidence(plant):
    home, repo, old, sha = plant
    (repo / "README.md").write_text("reader front page")
    target = repo / "src/mishe_tauftauf/wake.py"
    target.parent.mkdir(parents=True); target.write_text("pass")
    git(repo, "add", "."); git(repo, "commit", "-qm", "readme and scheduler")
    key = "wake-docs-mixed"
    write_json(yielding.snapshot_path(home, key), dict(role="docs", snapshot=owed.Snapshot().to_json()))
    with pytest.raises(ValueError, match="high-impact request requires a reviewed strong patch"):
        q.enqueue(home, repo, git(repo, "rev-parse", "HEAD"), previous=old, role="docs", key=key,
                  check=[sys.executable, "-c", "assert True"],
                  observe=[sys.executable, "-c", "assert True"])


def test_delivery_monitor_expires_to_unknown(plant):
    from mishe_tauftauf import signal_store
    home, *_ = plant
    q.heartbeat(home)
    assert signal_store.read(home, q.SIGNAL, now=0).value == "GREEN"
    assert signal_store.read(home, q.SIGNAL, now=10**12).state != "OK"


def test_foreign_check_cannot_replace_plan_after_consumer_validates(plant, monkeypatch):
    from mishe_tauftauf import wall_patch
    home, *_ = plant; strong_request(plant, monkeypatch)
    original = q.validate_source
    def attempted_race(*a):
        source = original(*a)
        with pytest.raises(ValueError, match="delivery admission"):
            wall_patch.check(home, "strong", [sys.executable, "-c", "pass"])
        return source
    monkeypatch.setattr(q, "validate_source", attempted_race)
    assert q.consume(home)[0]["state"] == "delivered"


def test_known_promotion_preflight_rejection_does_not_park_all_minds(plant, monkeypatch):
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    def reject(*a, **kw):
        raise ValueError("previous SHA does not match installed wake consumer pin")
    monkeypatch.setattr(rp, "promote", reject)
    assert q.consume(home)[0]["state"] == "failed"
    assert not q.admission_path(home).exists()


def test_strong_activation_timeout_observes_successful_recovery(plant, monkeypatch):
    from mishe_tauftauf import wall_patch
    home, *_ = plant; strong_request(plant, monkeypatch)
    original = wall_patch.run; calls = []
    def timeout_once(home, identity, label, command):
        calls.append(label)
        if label == "activate" and calls.count(label) == 1:
            raise subprocess.TimeoutExpired(command, 300)
        return original(home, identity, label, command)
    monkeypatch.setattr(wall_patch, "run", timeout_once)
    result = q.consume(home)[0]
    assert result["state"] == "failed"
    assert result["recovery_observation"]["code"] == 0
    assert not q.admission_path(home).exists()


def test_recovery_observation_timeout_stays_unknown_without_replay(plant, monkeypatch):
    from mishe_tauftauf import wall_patch
    home, *_ = plant; strong_request(plant, monkeypatch, bad_new=True)
    original = wall_patch.run
    def timeout_recovery(home, identity, label, command):
        if label == "request-recovery-observe":
            raise subprocess.TimeoutExpired(command, 300)
        return original(home, identity, label, command)
    monkeypatch.setattr(wall_patch, "run", timeout_recovery)
    result = q.consume(home)[0]
    assert result["state"] == "UNKNOWN"
    assert result["recovery_observation"]["code"] is None
    assert q.admission_path(home).exists()


def test_dead_owner_admission_is_released_by_next_consume_as_unknown(plant, monkeypatch):
    monkeypatch.setattr(q, "notify", lambda *a, **k: None)
    """Incident 2026-11-10: a dead owner's admission blocked every wake (held) with no release path."""
    import subprocess as sp
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    dead = sp.Popen([sys.executable, "-c", "pass"]); dead.wait()
    stale = time.time() - q.ADMISSION_MAX_AGE_SECONDS - 60
    write_json(q.location(home, row["id"]), dict(row, state="running", started_at=stale))
    write_json(q.admission_path(home), dict(id=row["id"], token="t", pid=dead.pid, at=stale))
    q.consume(home)
    assert not q.admission_path(home).exists()
    kept = read_json(q.location(home, row["id"]))
    assert kept["state"] == "UNKNOWN" and "admission released" in kept["error"]
    assert q.standing_problem(home) is not None  # UNKNOWN stays visible to the resolver
    owed.touch_inbox(home, "docs")
    class Engine:
        def run(self, *a, **kw): return None
    result = wake.run_wake(home, "docs", roles=["docs"], engine=Engine(), now=100, repo=plant[1],
                           mail_reader=lambda *a, **k: [])
    assert result.outcome != "held"


def test_live_owner_admission_is_never_released_by_consume(plant, monkeypatch):
    monkeypatch.setattr(q, "notify", lambda *a, **k: None)
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    stale = time.time() - q.ADMISSION_MAX_AGE_SECONDS - 60
    write_json(q.location(home, row["id"]), dict(row, state="running", started_at=stale))
    write_json(q.admission_path(home), dict(id=row["id"], token="t", pid=os.getpid(), at=stale))
    q.consume(home)
    assert q.admission_path(home).exists()
    assert read_json(q.location(home, row["id"]))["state"] == "UNKNOWN"


def test_patch_drift_before_apply_fails_without_admission_or_runtime_write(plant, monkeypatch):
    """Incident 2026-11-10: a reviewed patch whose files drifted raised before any write but ended UNKNOWN."""
    from mishe_tauftauf import wall_patch
    monkeypatch.setattr(q, "notify", lambda *a, **k: None)
    # Simulate drift after the request-time check: only the apply-time check (wall_patch._apply) must catch it.
    monkeypatch.setattr(q, "validate_source", lambda *a, **k: None)
    home, *_ = plant; row, runtime = strong_request(plant, monkeypatch)
    record = wall_patch.status(home, "strong")
    assert record["phase"] == "reviewed"
    name = next(iter(record["files"]))
    record["files"][name]["after"] = "drifted-after-review"
    write_json(wall_patch.location(home, "strong"), record)
    before = (runtime / "docs/page.md").read_text()
    result = q.consume(home)[0]
    assert result["state"] == "failed" and "patch changed since review" in result["error"]
    assert (runtime / "docs/page.md").read_text() == before
    assert not q.admission_path(home).exists()
    assert wall_patch.status(home, "strong")["phase"] == "reviewed"


def test_interrupt_before_effect_releases_admission_and_keeps_request_queued(plant, monkeypatch):
    """Operator follow-up 2026-11-10: an interrupt before any effect must not leave admission or UNKNOWN."""
    monkeypatch.setattr(q, "notify", lambda *a, **k: None)
    home, *_ = plant; row = request(plant); settle(home, row["key"])
    def interrupt(*a, **kw):
        raise KeyboardInterrupt()
    monkeypatch.setattr(q, "validate_source", interrupt)
    with pytest.raises(KeyboardInterrupt): q.consume(home)
    assert not q.admission_path(home).exists()
    kept = read_json(q.location(home, row["id"]))
    assert kept["state"] == "queued" and "ended_at" not in kept
