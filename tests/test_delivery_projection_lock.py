"""Real bare-origin fixture; only private reviewer latency is controlled."""
import fcntl
import threading
import json
import time
import pytest

from mishe_tauftauf import delivery, task_state
from mishe_tauftauf.feed import Feed

from tests.test_delivery import candidate, ci, submit


def run_blocked(call, monkeypatch, *, body_prefix=None):
    entered, release = threading.Event(), threading.Event()
    errors = []
    from mishe_tauftauf import post_check
    original = post_check.require_prose
    def reviewer(*args, **kwargs):
        if body_prefix and not args[2].startswith(body_prefix):
            return original(*args, **kwargs)
        entered.set()
        assert release.wait(5), "test admission was not released"
        return original(*args, **kwargs)
    monkeypatch.setattr(post_check, "require_prose", reviewer)
    def execute():
        try:
            call()
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=execute)
    worker.start()
    assert entered.wait(5)
    return worker, release, errors


def test_slow_author_wait_does_not_hold_global_delivery_lock(candidate, monkeypatch):
    home = candidate[0]
    worker, release, errors = run_blocked(lambda: submit(candidate), monkeypatch)
    try:
        with (home / "deliveries/.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors, errors


def test_slow_ready_projection_is_rejected_if_facts_change(candidate, monkeypatch):
    home, _, _, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    worker, release, errors = run_blocked(lambda: delivery.check(home, "repair"), monkeypatch)
    try:
        with (home / "deliveries/.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = delivery.load(home, "repair")
            current.update(phase="blocked", reason="new exact CI evidence requires repair")
            delivery._save(home, current)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert errors, "stale projection unexpectedly committed"
    assert "delivery changed" in str(errors[0]), errors
    integration = delivery._integration_id(delivery.load(home, "repair"))
    assert integration not in task_state.registry(Feed(home).entries())
    assert delivery.load(home, "repair")["phase"] == "blocked"


def test_other_ready_candidate_pushes_while_projection_review_waits(candidate, monkeypatch):
    from tests.test_delivery import git
    home, primary, _, base, head, _ = candidate
    submit(candidate)
    work = primary.parent / "candidate-b"
    git(primary, "worktree", "add", "-q", "-b", "candidate-b", str(work), base)
    (work / "other.txt").write_text("candidate B")
    git(work, "add", "other.txt")
    git(work, "commit", "-qm", "candidate B")
    other_head = git(work, "rev-parse", "HEAD")
    git(work, "push", "-q", "origin", "candidate-b")
    review = primary.parent / "review-b.json"
    review.write_text(json.dumps(dict(base=base, head=other_head, reviewer="witness", verdict="pass")))
    delivery.submit(home, "repair-b", "health", work, base, "candidate-b", review)
    monkeypatch.setattr(delivery, "read_ci", lambda repo, sha, branch: ci(sha))
    delivery.check(home, "repair-b")
    first, release, first_errors = run_blocked(lambda: delivery.check(home, "repair"), monkeypatch)
    other_errors = []
    def integrate_other():
        try:
            delivery.integrate(home, "repair-b", "operator")
        except BaseException as exc:
            other_errors.append(exc)
    second = threading.Thread(target=integrate_other)
    second.start()
    try:
        deadline = time.monotonic() + 3
        while (git(primary, "ls-remote", "origin", "refs/heads/main").split()[0] != other_head
               or delivery.load(home, "repair-b")["phase"] != "integrated"):
            assert time.monotonic() < deadline, "independent ready main push is trapped behind projection admission"
            time.sleep(0.01)
        assert not release.is_set()
        assert delivery.load(home, "repair-b")["phase"] == "integrated"
    finally:
        release.set()
        first.join(5)
        second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert not first_errors and not other_errors, (first_errors, other_errors)


def test_claim_admission_releases_fact_lock_and_rechecks_integration(candidate, monkeypatch):
    from tests.test_mind_choice import wake
    home, _, _, _, head, proof = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    integration = delivery._integration_id(delivery.load(home, "repair"))
    attempt = wake(home, "genome")
    worker, release, errors = run_blocked(lambda: task_state.claim(home, integration, "genome", attempt,
        "The exact reviewed ready source can be integrated.", proof), monkeypatch, body_prefix="[task-claim]")
    try:
        with (home / "deliveries/.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = delivery.load(home, "repair")
            current.update(phase="blocked", reason="CI evidence changed during claim admission")
            delivery._save(home, current)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert errors
    assert "delivery changed" in str(errors[0]), errors
    assert not task_state.pending_tasks(Feed(home).entries())


@pytest.mark.parametrize("integrated", [False, True])
def test_recovery_binds_new_author_wait_once_after_saved_submission(candidate, monkeypatch, integrated):
    from tests.test_delivery import git
    home, primary, _, _, head, _ = candidate
    original = delivery._wait_owner
    def crash(*args):
        raise ValueError("crash after delivery save before author wait")
    monkeypatch.setattr(delivery, "_wait_owner", crash)
    with pytest.raises(ValueError, match="crash"):
        submit(candidate)
    assert "repair" not in task_state.registry(Feed(home).entries())
    monkeypatch.setattr(delivery, "_wait_owner", original)
    if integrated:
        git(primary, "push", "-q", "origin", f"{head}:refs/heads/main")
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head, "pass" if integrated else "fail"))
    record = delivery.check(home, "repair")
    entries = Feed(home).entries()
    author = task_state.registry(entries)["repair"]
    assert record["phase"] == ("integrated" if integrated else "blocked")
    assert record["author_retry_wait_sequence"] == author.sequence
    assert task_state.eligible(author, entries)
    notices = [e for e in entries if e.body.startswith("[task-event] delivery-repair-updated\n")]
    assert len(notices) == 1
    delivery.check(home, "repair")
    assert len([e for e in Feed(home).entries() if e.body.startswith("[task-event] delivery-repair-updated\n")]) == 1


def test_recovery_preserves_newer_author_wait_after_admission(candidate, monkeypatch):
    home, _, _, _, head, proof = candidate
    original = delivery._wait_owner
    def crash(*args):
        raise ValueError("crash before author registration")
    monkeypatch.setattr(delivery, "_wait_owner", crash)
    with pytest.raises(ValueError, match="crash"):
        submit(candidate)
    newer = []
    def racing_wait(home, record):
        entry = original(home, record)
        newer.append(task_state.wait_for(home, "repair", "senses", "Inspect a new prerequisite", "New author wait after recovery admission", proof, retry_event="delivery-repair-updated"))
        return entry
    monkeypatch.setattr(delivery, "_wait_owner", racing_wait)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head, "fail"))
    with pytest.raises(ValueError, match="newer registration"):
        delivery.check(home, "repair")
    monkeypatch.setattr(delivery, "_wait_owner", original)
    delivery.check(home, "repair")
    entries = Feed(home).entries()
    author = task_state.registry(entries)["repair"]
    assert author.sequence == newer[0].sequence
    assert not task_state.eligible(author, entries)
    assert not any(e.body.startswith("[task-event] delivery-repair-updated\n") for e in entries)


def test_recovery_of_committed_wait_preserves_its_exact_sequence(candidate, monkeypatch):
    home, _, _, _, head, _ = candidate
    original = delivery._complete_owner_wait
    def crash(*args):
        raise ValueError("crash after wait flush before retry binding")
    monkeypatch.setattr(delivery, "_complete_owner_wait", crash)
    with pytest.raises(ValueError, match="crash"):
        submit(candidate)
    entries = Feed(home).entries()
    author = task_state.registry(entries)["repair"]
    expected = author.sequence
    assert delivery.load(home, "repair")["author_wait_projection"]["sequence"] == expected
    monkeypatch.setattr(delivery, "_complete_owner_wait", original)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head, "fail"))
    record = delivery.check(home, "repair")
    entries = Feed(home).entries()
    author = task_state.registry(entries)["repair"]
    assert author.sequence == expected
    assert record["author_retry_wait_sequence"] == expected
    assert task_state.eligible(author, entries)


def test_recovery_completes_partial_author_registration(candidate, monkeypatch):
    home, _, _, _, head, _ = candidate
    original = task_state.wait_for
    def crash(*args, **kwargs):
        raise ValueError("crash after author add before wait admission")
    monkeypatch.setattr(task_state, "wait_for", crash)
    with pytest.raises(ValueError, match="crash"):
        submit(candidate)
    assert task_state.registry(Feed(home).entries())["repair"].status == "ready"
    monkeypatch.setattr(task_state, "wait_for", original)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head, "fail"))
    record = delivery.check(home, "repair")
    entries = Feed(home).entries()
    author = task_state.registry(entries)["repair"]
    assert author.status == "waiting"
    assert record["author_retry_wait_sequence"] == author.sequence
    assert task_state.eligible(author, entries)


@pytest.mark.parametrize("newer_wait", [False, True])
def test_same_submission_recovers_checkpoint_without_replacing_wait(candidate, monkeypatch, newer_wait):
    home, _, _, _, _, proof = candidate
    original = delivery._complete_owner_wait
    def crash(*args):
        raise ValueError("crash after committed wait before binding")
    monkeypatch.setattr(delivery, "_complete_owner_wait", crash)
    with pytest.raises(ValueError, match="crash"):
        submit(candidate)
    if newer_wait:
        task_state.wait_for(home, "repair", "senses", "Preserve new prerequisite",
            "An independent producer must finish", proof, retry_event="new-prerequisite")
    expected = task_state.registry(Feed(home).entries())["repair"]
    monkeypatch.setattr(delivery, "_complete_owner_wait", original)
    submit(candidate)
    actual = task_state.registry(Feed(home).entries())["repair"]
    assert actual == expected
    assert not delivery.load(home, "repair")["author_wait_pending"]
    assert not task_state.eligible(actual, Feed(home).entries())
