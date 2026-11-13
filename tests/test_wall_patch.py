import json
import sys
import pytest
from mishe_tauftauf import wall_patch
import subprocess



def _req_file(tmp_path):
    # The reviewer receives its prompt file as stdin, so the file must exist.
    path = tmp_path / "req.txt"
    path.write_text("prompt")
    return path

def layout(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "runtime": str(runtime)}))
    source = tmp_path / "src/mishe_tauftauf"
    source.mkdir(parents=True)
    deployed = runtime / "src/mishe_tauftauf"
    deployed.mkdir(parents=True)
    (source / "example.py").write_text("before\n")
    (deployed / "example.py").write_text("before\n")
    return home, source / "example.py", deployed / "example.py"


def test_patch_rejected_review_does_not_activate(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": False, "reason": "unsafe"})
    with pytest.raises(ValueError, match="review"):
        wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    assert deployed.read_text() == "before\n"


def test_failed_observation_reverts_runtime_and_keeps_draft(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    with pytest.raises(ValueError, match="observation"):
        wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], [sys.executable, "-c", "raise SystemExit(1)"])
    assert deployed.read_text() == "before\n"
    assert source.read_text() == "after\n"
    assert "reverted" in wall_patch.status(home, "change")["phase"]


def test_changed_patch_requires_fresh_review(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    source.write_text("unreviewed\n")
    with pytest.raises(ValueError, match="changed"):
        wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"])
    assert deployed.read_text() == "before\n"


def test_check_that_mutates_source_is_not_reviewed(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: pytest.fail("must not review untested bytes"))
    with pytest.raises(ValueError, match="changed during"):
        wall_patch.check(home, "change", [sys.executable, "-c", f"from pathlib import Path; Path({str(source)!r}).write_text('mutated')"])
    failed = wall_patch.status(home, "change")
    assert failed["phase"] == "test-mutated-source"
    assert "src/mishe_tauftauf/example.py" in failed["failure"], "the failure must name the mutated file"


def test_rollback_timeout_is_saved_as_visible_failure(tmp_path, monkeypatch):
    import subprocess
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    original = wall_patch.run
    def failing_run(home, identity, label, command):
        if label == "revert":
            raise subprocess.TimeoutExpired(command, 300)
        return original(home, identity, label, command)
    monkeypatch.setattr(wall_patch, "run", failing_run)
    with pytest.raises(subprocess.TimeoutExpired):
        wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], [sys.executable, "-c", "raise SystemExit(1)"])
    assert wall_patch.status(home, "change")["phase"] == "revert-failed"
    assert deployed.read_text() == "before\n"


def test_unexpected_runtime_bytes_are_preserved_with_visible_rollback_failure(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    activation = [sys.executable, "-c", f"from pathlib import Path; Path({str(deployed)!r}).write_text('unexpected')"]
    with pytest.raises(ValueError, match="runtime changed"):
        wall_patch.apply(home, "change", activation, [sys.executable, "-c", "raise SystemExit(1)"])
    record = wall_patch.status(home, "change")
    assert record["phase"] == "revert-failed"
    assert "observation failed" in record["apply_failure"]
    assert "runtime changed" in record["rollback_failure"]
    assert deployed.read_text() == "unexpected"


def test_explicit_revert_returns_failure_when_consumer_restart_fails(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"])
    with pytest.raises(ValueError, match="revert activation failed"):
        wall_patch.revert(home, "change", [sys.executable, "-c", "raise SystemExit(1)"])
    assert deployed.read_text() == "before\n"
    assert wall_patch.status(home, "change")["phase"] == "revert-failed"


def test_successful_revert_clears_stale_rollback_failure(tmp_path, monkeypatch):
    # A failed revert leaves `rollback_failure`; a later successful revert must
    # not keep the old failure beside a healthy reverted record. The verified
    # health-pane-lease-freshness record carried exactly that stale marker after
    # its final verify revert.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"])
    with pytest.raises(ValueError, match="revert activation failed"):
        wall_patch.revert(home, "change", [sys.executable, "-c", "raise SystemExit(1)"])
    failed = wall_patch.status(home, "change")
    assert failed["phase"] == "revert-failed" and failed["rollback_failure"]
    wall_patch.revert(home, "change", [sys.executable, "-c", "pass"])
    recovered = wall_patch.status(home, "change")
    assert recovered["phase"] == "reverted"
    assert "rollback_failure" not in recovered


def test_failed_activation_of_new_runtime_file_removes_it_and_preserves_source(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    name = "src/mishe_tauftauf/added.py"
    wall_patch.prepare(home, "added", [name])
    draft = tmp_path / name
    draft.write_text("new module\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "added", [sys.executable, "-c", "pass"])
    assert wall_patch.status(home, "added")["files"][name]["after"] is not None
    with pytest.raises(ValueError, match="observation failed"):
        wall_patch.apply(home, "added", [sys.executable, "-c", "pass"], [sys.executable, "-c", "raise SystemExit(1)"])
    assert not (tmp_path / "runtime" / name).exists()
    assert draft.read_text() == "new module\n"
    assert wall_patch.status(home, "added")["phase"] == "reverted"


def test_delivery_requires_successful_revert_observation_and_reapply(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    activate = [sys.executable, "-c", "pass"]
    observe = [sys.executable, "-c", f"from pathlib import Path; assert Path({str(deployed)!r}).read_text() == 'after\\n'"]
    reverted = [sys.executable, "-c", f"from pathlib import Path; assert Path({str(deployed)!r}).read_text() == 'before\\n'"]
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"], activate=activate, observe=observe, revert_observe=reverted)
    wall_patch.apply(home, "change", activate, observe)
    assert not wall_patch.status(home, "change").get("delivery_verified")
    wall_patch.verify(home, "change")
    record = wall_patch.status(home, "change")
    assert record["phase"] == "applied" and record["delivery_verified"]
    assert wall_patch.verified_delivery(record)
    assert record["revert_observation"]["code"] == 0
    assert record["verification"]["patch_hash"]
    assert deployed.read_text() == "after\n"

def test_verified_delivery_requires_the_verification_binding():
    assert not wall_patch.verified_delivery({"phase": "applied", "delivery_verified": True})
    assert not wall_patch.verified_delivery({"phase": "applied"})
    assert not wall_patch.verified_delivery({"phase": "applied", "delivery_verified": True, "verification": "yes"})
    assert wall_patch.verified_delivery({"phase": "applied", "delivery_verified": True,
                                         "verification": {"at": "2026-10-01T00:00:00+00:00"}})


def test_verified_delivery_clears_a_stale_apply_failure(tmp_path, monkeypatch):
    # An earlier failed apply leaves apply_failure on the record; a later
    # successful verify must clear it so the ledger cannot read as verified and
    # broken at once.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    command = [sys.executable, "-c", "pass"]
    observe = [sys.executable, "-c", f"from pathlib import Path; assert Path({str(deployed)!r}).read_text() == 'after\\n'"]
    reverted = [sys.executable, "-c", f"from pathlib import Path; assert Path({str(deployed)!r}).read_text() == 'before\\n'"]
    wall_patch.check(home, "change", command, activate=command, observe=observe, revert_observe=reverted)
    original = wall_patch.run
    def failed_observe(home_, identity, label, cmd):
        if label == "observe":
            return {"code": 1, "output": "boom"}
        return original(home_, identity, label, cmd)
    monkeypatch.setattr(wall_patch, "run", failed_observe)
    with pytest.raises(ValueError, match="observation"):
        wall_patch.apply(home, "change", command, observe)
    assert wall_patch.status(home, "change")["apply_failure"]
    monkeypatch.setattr(wall_patch, "run", original)
    wall_patch.check(home, "change", command, activate=command, observe=observe, revert_observe=reverted)
    wall_patch.apply(home, "change", command, observe)
    wall_patch.verify(home, "change")
    record = wall_patch.status(home, "change")
    assert record["delivery_verified"] is True
    assert "apply_failure" not in record and "failure" not in record


def test_aborted_check_leaves_no_delivery_verdict(tmp_path, monkeypatch):
    # A review-unavailable attempt never produced a delivery verdict, so the
    # ledger must not carry delivery_verified=False as if delivery had failed.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    def unavailable(*a):
        raise ValueError("reviewer down")
    monkeypatch.setattr(wall_patch, "review", unavailable)
    with pytest.raises(ValueError, match="reviewer down"):
        wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    record = wall_patch.status(home, "change")
    assert record["phase"] == "review-unavailable"
    assert "delivery_verified" not in record

def test_delivery_does_not_certify_failed_revert_observation(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    command = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", command, activate=command, observe=command,
                     revert_observe=[sys.executable, "-c", "raise SystemExit(1)"])
    wall_patch.apply(home, "change", command, command)
    with pytest.raises(ValueError, match="revert observation"):
        wall_patch.verify(home, "change")
    assert not wall_patch.status(home, "change").get("delivery_verified")
    assert wall_patch.status(home, "change")["phase"] == "revert-observation-failed"
    assert deployed.read_text() == "before\n"


def test_activation_refused_inside_the_wake_holding_the_dispatch_lock(tmp_path, monkeypatch):
    import fcntl
    from mishe_tauftauf.core.state import DispatchStore
    home, source, deployed = layout(tmp_path)
    lock_file = DispatchStore(home / "dispatch").lock_path.open("a+")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    try:
        monkeypatch.setenv("MISHE_WAKE_KEY", "wake-own")
        with pytest.raises(ValueError, match="holds the dispatch lock"):
            wall_patch.refuse_inside_own_wake(home)
        command = [sys.executable, "-c", "pass"]
        with pytest.raises(ValueError, match="holds the dispatch lock"):
            wall_patch.apply(home, "change", command, command)
        with pytest.raises(ValueError, match="holds the dispatch lock"):
            wall_patch.revert(home, "change", command)
        monkeypatch.delenv("MISHE_WAKE_KEY")
        wall_patch.refuse_inside_own_wake(home)  # outside a wake the guard is silent
    finally:
        lock_file.close()


def test_activation_allowed_inside_a_wake_when_dispatch_lock_is_free(tmp_path, monkeypatch):
    from mishe_tauftauf.core.state import DispatchStore
    home, source, deployed = layout(tmp_path)
    DispatchStore(home / "dispatch")
    monkeypatch.setenv("MISHE_WAKE_KEY", "wake-consumer")
    wall_patch.refuse_inside_own_wake(home)  # free lock: the guard lets it through
    monkeypatch.delenv("MISHE_WAKE_KEY")


def test_reviewed_delivery_script_cannot_change_before_apply(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    script = home / "activate.py"
    script.write_text("print('restart')\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    activate = [sys.executable, str(script)]
    command = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", command, activate=activate, observe=command, revert_observe=command)
    script.write_text("raise SystemExit(1)\n")
    with pytest.raises(ValueError, match="delivery command"):
        wall_patch.apply(home, "change", activate, command)
    assert deployed.read_text() == "before\n"


def test_changed_live_bytes_cannot_be_certified(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    command = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", command, activate=command, observe=command, revert_observe=command)
    wall_patch.apply(home, "change", command, command)
    deployed.write_text("another writer\n")
    with pytest.raises(ValueError, match="runtime"):
        wall_patch.verify(home, "change")
    assert deployed.read_text() == "another writer\n"
    assert not wall_patch.status(home, "change").get("delivery_verified")


def test_failed_reapply_preserves_accurate_recovered_phase(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    command = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", command, activate=command, observe=command, revert_observe=command)
    wall_patch.apply(home, "change", command, command)
    original = wall_patch.run
    def failed_reapply(home, identity, label, command):
        if label == "activate":
            return original(home, identity, label, [sys.executable, "-c", "raise SystemExit(1)"])
        return original(home, identity, label, command)
    monkeypatch.setattr(wall_patch, "run", failed_reapply)
    with pytest.raises(ValueError, match="activation/observation"):
        wall_patch.verify(home, "change")
    record = wall_patch.status(home, "change")
    assert record["phase"] == "reverted"
    assert record["revert_observation"]["code"] == 0
    assert record["apply_failure"] and record["verification_failure"]
    assert not record["delivery_verified"]
    assert deployed.read_text() == "before\n"


def test_owned_checkout_delivery_script_cannot_change_after_review(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    script = tmp_path / "activate.py"
    script.write_text("print('restart')\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    activate = [sys.executable, str(script)]
    command = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", command, activate=activate, observe=command, revert_observe=command)
    script.write_text("raise SystemExit(1)\n")
    with pytest.raises(ValueError, match="delivery command"):
        wall_patch.apply(home, "change", activate, command)
    assert deployed.read_text() == "before\n"


def test_successful_recheck_clears_stale_failure(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    command = [sys.executable, "-c", "pass"]

    def unavailable(*args):
        raise OSError("reviewer usage limit reached")

    monkeypatch.setattr(wall_patch, "review", unavailable)
    with pytest.raises(OSError, match="usage limit"):
        wall_patch.check(home, "change", command)
    failed = wall_patch.status(home, "change")
    assert failed["phase"] == "review-unavailable" and failed["failure"]

    # Once the reviewer recovers, a fresh successful check must not keep
    # rendering the old failure beside a healthy reviewed patch.
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", command)
    recovered = wall_patch.status(home, "change")
    assert recovered["phase"] == "reviewed"
    assert "failure" not in recovered


def test_reviewer_model_honors_site_config_and_env_override(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_review

    monkeypatch.delenv("MISHE_WALL_REVIEW_MODEL", raising=False)
    home = tmp_path / "site"
    home.mkdir()
    assert wall_review.reviewer_model(home) == wall_review.DEFAULT_MODEL == "claude-haiku-5-5"
    (home / "patch-review.json").write_text(json.dumps({"model": "claude-haiku-4-6"}))
    assert wall_review.reviewer_model(home) == "claude-haiku-4-6"
    monkeypatch.setenv("MISHE_WALL_REVIEW_MODEL", "claude-haiku-5-5-x")
    assert wall_review.reviewer_model(home) == "claude-haiku-5-5-x"


def test_reviewer_refuses_non_haiku_models_and_runs_tool_free_claude(tmp_path, monkeypatch):
    # Ilya DM 300461: the patch reviewer is Haiku through the claude engine, never Sonnet
    # or a RouterAI route. A non-Haiku id is refused, not silently run.
    from mishe_tauftauf import wall_review
    import pytest

    monkeypatch.delenv("MISHE_WALL_REVIEW_MODEL", raising=False)
    home = tmp_path / "site"
    home.mkdir()
    for bad in ("routerai/z-ai/glm-5.3-flash", "claude-sonnet-5-5", "opencode-go/longcat-2.5-preview-free"):
        (home / "patch-review.json").write_text(json.dumps({"model": bad}))
        with pytest.raises(ValueError):
            wall_review.reviewer_model(home)
    argv = wall_review.reviewer_command("claude-haiku-5-5")
    assert argv[1:3] == ["-p", "--model"] and argv[3] == "claude-haiku-5-5"
    assert argv[argv.index("--tools") + 1] == ""
    assert "omp" not in argv[0]
    with pytest.raises(ValueError):
        wall_review.reviewer_command("claude-sonnet-5-5")


def test_recorded_invalid_evidence_answer_is_retried_then_unavailable(tmp_path, monkeypatch):
    # A recorded answer citing evidence outside the request (the 12 Nov glm failure mode)
    # is a failed attempt. Retried answers that stay invalid raise, so the gate records
    # review-unavailable; a bad answer never passes as clear.
    from mishe_tauftauf import wall_review
    import importlib
    import pytest

    invalid = json.dumps({"version": 1, "input_hash": "h", "results": [
        {"id": "q1", "verdict": "clear", "reason": "ok", "evidence": ["invented or unrelated evidence reference"]}]})
    runs = []

    def runner(*args, **kwargs):
        runs.append(kwargs)
        return type("R", (), {"returncode": 0, "stdout": invalid, "stderr": ""})()

    def validate(answer):
        allowed = {"ref-a"}
        for row in answer["results"]:
            if any(ref not in allowed for ref in row["evidence"]):
                raise ValueError("invented or unrelated evidence reference")

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    with pytest.raises(RuntimeError, match="invalid answer on 3 attempts"):
        wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path),
                                  runner=runner, validate=validate)
    importlib.reload(wall_review)
    assert len(runs) == wall_review.MAX_EMPTY_RETRIES
    assert all(run["stdin"] is not None for run in runs)


def test_reviewer_timeout_honors_site_config_and_env_override(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_review
    import importlib

    monkeypatch.delenv("MISHE_WALL_REVIEW_TIMEOUT", raising=False)
    home = tmp_path / "site"
    home.mkdir()
    importlib.reload(wall_review)
    assert wall_review.reviewer_timeout(home) == wall_review.DEFAULT_TIMEOUT
    (home / "patch-review.json").write_text(json.dumps({"timeout_seconds": 420}))
    assert wall_review.reviewer_timeout(home) == 420
    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "300")
    assert wall_review.reviewer_timeout(home) == 300.0
    for bad in ("0", "-1", "601", "x"):
        monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", bad)
        with pytest.raises(ValueError, match="reviewer timeout"):
            wall_review.reviewer_timeout(home)


def test_outer_worker_budget_exceeds_configured_reviewer_budget():
    # A reviewer allowed 420s by patch-review.json must not be killed at the old
    # 295s outer clamp; the outer budget settles strictly above the model call.
    assert wall_patch._worker_timeout({}) == 295 + wall_patch.WORKER_SETTLE_SECONDS
    assert wall_patch._worker_timeout({"timeout_seconds": 420}) == 480
    for bad in (0, -1, 601, "x", True, None):
        with pytest.raises(ValueError, match="timeout_seconds"):
            wall_patch._worker_timeout({"timeout_seconds": bad})


def test_review_passes_the_configured_outer_budget_to_the_worker(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    (home / "patch-review.json").write_text(json.dumps(
        {"command": [sys.executable, "-c", "pass"], "timeout_seconds": 420}))
    seen = {}

    def spy(command, encoded, timeout):
        seen["timeout"] = timeout
        request = json.loads(encoded.decode())
        seen["question"] = request["questions"][0]["question"]
        return json.dumps({"version": 1, "input_hash": request["input_hash"],
                           "results": [{"id": "PATCH", "verdict": "clear", "reason": "ok", "evidence": []}]}).encode()

    monkeypatch.setattr(wall_patch, "_worker", spy)
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    assert seen["timeout"] == 480
    assert "pre-activation review" in seen["question"]
    assert "delivery_verified" in seen["question"]
    assert "not yet expected" in seen["question"]
    assert wall_patch.status(home, "change")["phase"] == "reviewed"


def test_failed_review_drops_the_stale_verdict(tmp_path, monkeypatch):
    # A superseding review flake overwrote the phase but kept an older passing
    # verdict, so the dashboard could render a healthy review under a failed phase.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    command = [sys.executable, "-c", "pass"]

    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", command)
    assert wall_patch.status(home, "change")["phase"] == "reviewed"

    def unavailable(*args):
        raise OSError("reviewer usage limit reached")

    monkeypatch.setattr(wall_patch, "review", unavailable)
    with pytest.raises(OSError, match="usage limit"):
        wall_patch.check(home, "change", command)
    failed = wall_patch.status(home, "change")
    assert failed["phase"] == "review-unavailable"
    assert failed["failure"]
    assert "review" not in failed, "a failed attempt must not keep the stale passing verdict"


def test_empty_reviewer_output_is_retried_then_succeeds(tmp_path, monkeypatch):
    # A reviewer can exit 0 with empty stdout, which json.loads turned into an
    # unattributable JSONDecodeError. Empty output must be retried, not fatal.
    from mishe_tauftauf import wall_review
    import importlib

    runs = []
    good = '{"version": 1, "input_hash": "h", "results": []}'

    def empty_then_ok(*args, **kwargs):
        runs.append(1)
        if len(runs) < wall_review.MAX_EMPTY_RETRIES:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": "Working..."})()
        return type("R", (), {"returncode": 0, "stdout": good, "stderr": ""})()

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    answer = wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path), runner=empty_then_ok)
    importlib.reload(wall_review)
    assert len(runs) == 3, "empty output must be retried, not accepted on the empty run"
    assert answer == json.loads(good)


def test_empty_reviewer_output_names_the_failure_when_retries_exhaust(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_review
    import importlib

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    with pytest.raises(RuntimeError, match="patch reader returned empty output"):
        wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path),
                                  runner=lambda *a, **k:
                                  type("R", (), {"returncode": 0, "stdout": "",
                                                 "stderr": "Working..."})())
    importlib.reload(wall_review)


def test_review_answer_reports_a_nonzero_reviewer_exit(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_review
    import importlib

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    with pytest.raises(RuntimeError, match="patch reader failed"):
        wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path),
                                  runner=lambda *a, **k:
                                  type("R", (), {"returncode": 2, "stdout": "",
                                                 "stderr": "boom"})())
    importlib.reload(wall_review)


def test_review_answer_retries_an_answer_that_fails_validation(tmp_path, monkeypatch):
    # A well-formed answer whose version/input_hash does not match the request used
    # to raise out of main() and record the whole patch as review-unavailable. It is
    # a failed attempt and must be retried inside the same budget, not accepted.
    from mishe_tauftauf import wall_review
    import importlib

    runs = []
    bad = '{"version": 1, "input_hash": "wrong", "results": []}'
    good = '{"version": 1, "input_hash": "right", "results": []}'

    def bad_then_ok(*args, **kwargs):
        runs.append(1)
        body = bad if len(runs) < wall_review.MAX_EMPTY_RETRIES else good
        return type("R", (), {"returncode": 0, "stdout": body, "stderr": ""})()

    def check(answer):
        if answer.get("input_hash") != "right":
            raise ValueError("review protocol/input hash mismatch")

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    answer = wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path),
                                       runner=bad_then_ok, validate=check)
    importlib.reload(wall_review)
    assert len(runs) == 3, "a mismatched answer must be retried, not accepted"
    assert answer == json.loads(good)


def test_review_answer_names_invalid_answer_when_retries_exhaust(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_review
    import importlib

    def reject(answer):
        raise ValueError("review protocol/input hash mismatch")

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    with pytest.raises(RuntimeError, match="invalid answer"):
        wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path),
                                  runner=lambda *a, **k:
                                  type("R", (), {"returncode": 0,
                                                 "stdout": '{"version": 1}', "stderr": ""})(),
                                  validate=reject)
    importlib.reload(wall_review)

def test_review_answer_retries_a_timed_out_reader_then_succeeds(tmp_path, monkeypatch):
    # A provider stall kills the call with TimeoutExpired; one uncaught timeout
    # recorded the whole patch as review-unavailable with no retry. A timeout is
    # a failed attempt and must be retried inside the same budget.
    import subprocess
    from mishe_tauftauf import wall_review
    import importlib

    runs = []
    good = '{"version": 1, "input_hash": "h", "results": []}'

    def stall_then_ok(*args, **kwargs):
        runs.append(kwargs.get("timeout"))
        if len(runs) < wall_review.MAX_EMPTY_RETRIES:
            raise subprocess.TimeoutExpired(cmd="omp", timeout=kwargs.get("timeout", 0))
        return type("R", (), {"returncode": 0, "stdout": good, "stderr": ""})()

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    answer = wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path), runner=stall_then_ok)
    importlib.reload(wall_review)
    assert len(runs) == 3, "a timed-out reader must be retried, not accepted on the stalled run"
    assert answer == json.loads(good)
    # Each attempt is capped at an even share of the budget (120/3 = 40s on the
    # first attempt), so the retries fit inside the same budget.
    assert all(timeout is not None and 0 < timeout <= 40 for timeout in runs)


def test_review_answer_names_the_timeout_when_retries_exhaust(tmp_path, monkeypatch):
    import subprocess
    from mishe_tauftauf import wall_review
    import importlib

    def stall(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="omp", timeout=kwargs.get("timeout", 0))

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    with pytest.raises(RuntimeError, match="timed out") as info:
        wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path), runner=stall)
    importlib.reload(wall_review)
    # The failure names each attempt's category, so a provider stall is not
    # confused with an invalid answer or an empty one.
    assert str(info.value).count("attempt ") == wall_review.MAX_EMPTY_RETRIES
    assert "timeout at cap" in str(info.value)


def test_review_answer_failure_history_names_each_attempt_category(tmp_path, monkeypatch):
    import subprocess
    from mishe_tauftauf import wall_review
    import importlib

    outcomes = iter([
        subprocess.TimeoutExpired(cmd="omp", timeout=40),
        type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        type("R", (), {"returncode": 0, "stdout": '{"version": 1}', "stderr": ""})(),
    ])

    def scripted(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def reject(answer):
        raise ValueError("review protocol/input hash mismatch")

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    with pytest.raises(RuntimeError, match="invalid answer") as info:
        wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path),
                                  runner=scripted, validate=reject)
    importlib.reload(wall_review)
    message = str(info.value)
    assert "attempt 1: timeout at cap" in message
    assert "attempt 2: empty after" in message
    assert "attempt 3: invalid after" in message


def test_failed_test_drops_the_stale_review_verdict(tmp_path, monkeypatch):
    # A test failure must not keep a verdict from an earlier review of the same
    # record, or the phase falls while the dashboard still reads the old verdict.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    passing = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", passing)
    assert wall_patch.status(home, "change")["phase"] == "reviewed"

    failing = [sys.executable, "-c", "raise SystemExit(3)"]
    with pytest.raises(ValueError, match="deterministic patch check failed"):
        wall_patch.check(home, "change", failing)
    failed = wall_patch.status(home, "change")
    assert failed["phase"] == "test-failed"
    assert "review" not in failed, "a failed test must not keep the stale passing verdict"


def test_legacy_apply_time_review_flake_drops_the_stale_verdict(tmp_path, monkeypatch):
    # The legacy apply path re-reviews a delivery plan; a flake there must not
    # leave the verdict of the earlier review, which certified different evidence.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    command = [sys.executable, "-c", "pass"]
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", command)
    assert wall_patch.status(home, "change")["phase"] == "reviewed"

    # Simulate a delivery plan that flaked at apply time by re-reviewing.
    record = wall_patch.status(home, "change")
    record.pop("delivery_plan", None)
    wall_patch._save(wall_patch.location(home, "change"), record)

    def unavailable(*args):
        raise OSError("reviewer usage limit reached")

    monkeypatch.setattr(wall_patch, "review", unavailable)
    with pytest.raises(OSError, match="usage limit"):
        wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], command)
    failed = wall_patch.status(home, "change")
    assert failed["phase"] == "review-unavailable"
    assert "review" not in failed, "a flaked apply-time review must not keep the stale verdict"


def test_mutated_source_drops_the_stale_review_verdict(tmp_path, monkeypatch):
    # A concurrent edit to the scoped source invalidates any earlier verdict, which
    # was over different bytes; the failed phase must not render that stale verdict.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    command = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", command)
    assert wall_patch.status(home, "change")["phase"] == "reviewed"

    # A legitimate writer edits the shared source *while* the check's test runs:
    # check() re-snapshots `after` at the top of the loop, so a pre-run edit is
    # absorbed as the new expected bytes and would not trip the guard.
    mutating = [sys.executable, "-c",
                f"open({str(source)!r}, 'a').write('# a peer edit\\n')"]
    with pytest.raises(ValueError, match="source changed during deterministic check"):
        wall_patch.check(home, "change", mutating)
    failed = wall_patch.status(home, "change")
    assert failed["phase"] == "test-mutated-source"
    assert "review" not in failed, "mutated bytes must not keep a verdict over the old bytes"


def test_review_body_carries_full_text_of_unchanged_dependency(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    dependency = tmp_path / "src/mishe_tauftauf/dependency.py"
    dependency.write_text("unchanged dependency text\n")
    (tmp_path / "runtime/src/mishe_tauftauf/dependency.py").write_text("unchanged dependency text\n")
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py", "src/mishe_tauftauf/dependency.py"])
    source.write_text("after\n")
    (home / "patch-review.json").write_text(json.dumps({"command": [sys.executable, "-c", "pass"]}))
    seen = {}

    def spy(command, encoded, timeout):
        seen["request"] = json.loads(encoded.decode())
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(wall_patch, "_worker", spy)
    record = wall_patch.status(home, "change")
    import base64
    for name, values in record["files"].items():
        text = (source if name.endswith("example.py") else dependency).read_text()
        values["after"] = {"bytes": base64.b64encode(text.encode()).decode(), "mode": 0o644}
    record["test"] = {"log": str(tmp_path / "test.log")}
    (tmp_path / "test.log").write_text("ok\n")
    with pytest.raises(RuntimeError, match="stop after capture"):
        wall_patch.review(home, record)
    files = seen["request"]["context"]["patch"]["files"]
    assert files["src/mishe_tauftauf/dependency.py"]["after_text"] == "unchanged dependency text\n"
    assert files["src/mishe_tauftauf/dependency.py"]["source_diff"] == ""


def test_verify_refused_inside_the_wake_holding_the_dispatch_lock(tmp_path, monkeypatch):
    import fcntl
    from mishe_tauftauf.core.state import DispatchStore
    home, source, deployed = layout(tmp_path)
    lock_file = DispatchStore(home / "dispatch").lock_path.open("a+")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    try:
        monkeypatch.setenv("MISHE_WAKE_KEY", "wake-own")
        with pytest.raises(ValueError, match="holds the dispatch lock"):
            wall_patch.verify(home, "change")
    finally:
        monkeypatch.delenv("MISHE_WAKE_KEY")
        lock_file.close()


def test_prepare_with_base_expresses_a_committed_change(tmp_path):
    import base64
    home, source, deployed = layout(tmp_path)
    name = "src/mishe_tauftauf/example.py"

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                               "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)

    git("init", "-q")
    git("add", "src")
    git("commit", "-qm", "before")
    source.write_text("after\n")
    git("add", "src")
    git("commit", "-qm", "after")

    wall_patch.prepare(home, "change", [name], base="HEAD~1")
    record = wall_patch.status(home, "change")
    assert record["base"] == "HEAD~1"
    before = base64.b64decode(record["files"][name]["before"]["bytes"]).decode()
    assert before == "before\n"
    assert record["files"][name]["before"]["bytes"] != base64.b64encode(source.read_bytes()).decode()


def test_prepare_without_base_keeps_working_tree_before(tmp_path):
    import base64
    home, source, deployed = layout(tmp_path)
    name = "src/mishe_tauftauf/example.py"
    wall_patch.prepare(home, "change", [name])
    record = wall_patch.status(home, "change")
    assert "base" not in record
    assert base64.b64decode(record["files"][name]["before"]["bytes"]).decode() == "before\n"


def test_base_rejects_option_like_ref_and_escaping_path(tmp_path):
    home, source, deployed = layout(tmp_path)
    with pytest.raises(ValueError, match="invalid base ref"):
        wall_patch.base_snapshot(home.parent, "--output=/tmp/x", "src/mishe_tauftauf/example.py")
    with pytest.raises(ValueError, match="invalid patch path"):
        wall_patch.base_snapshot(home.parent, "HEAD", "../escape.py")


def git_layout(tmp_path):
    """A layout whose shared checkout is a real Git repository with a base commit."""
    home, source, deployed = layout(tmp_path)
    git = ["git", "-C", str(tmp_path), "-c", "user.email=t@example", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(git + ["add", "src"], check=True)
    subprocess.run(git + ["commit", "-qm", "base"], check=True)
    return home, source, deployed


def commit_peer(root, *paths):
    git = ["git", "-C", str(root), "-c", "user.email=t@example", "-c", "user.name=t"]
    subprocess.run(git + ["add", *paths], check=True)
    subprocess.run(git + ["commit", "-qm", "peer"], check=True)


def test_peer_commit_to_a_manifest_path_does_not_refuse_apply(tmp_path, monkeypatch):
    """The delivered artifact is the commit the release is materialized from.

    A cooperating writer committing to a manifest path during the check window moves
    the shared checkout and HEAD together. That is not drift of the delivered bytes,
    and refusing it cost five consecutive moving-pin cycles on 2026-11-11.
    """
    home, source, deployed = git_layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    source.write_text("peer\n")
    commit_peer(tmp_path, "src/mishe_tauftauf/example.py")
    wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"])
    assert wall_patch.status(home, "change")["phase"] == "applied"
    assert deployed.read_text() == "after\n"


def test_uncommitted_edit_to_a_manifest_path_still_refuses_apply(tmp_path, monkeypatch):
    home, source, deployed = git_layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    source.write_text("uncommitted\n")
    with pytest.raises(ValueError, match="changed since review"):
        wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"])
    assert deployed.read_text() == "before\n"


def test_peer_commit_during_check_is_not_a_source_mutation(tmp_path, monkeypatch):
    """A peer's commit during the test window is not the test mutating source."""
    home, source, deployed = git_layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    escaped = f"import subprocess,pathlib;pathlib.Path({str(source)!r}).write_text('peer\\n');subprocess.run(['git','-C',{str(tmp_path)!r},'-c','user.email=t@example','-c','user.name=t','add','src'],check=True);subprocess.run(['git','-C',{str(tmp_path)!r},'-c','user.email=t@example','-c','user.name=t','commit','-qm','peer'],check=True)"
    wall_patch.check(home, "change", [sys.executable, "-c", escaped])
    assert wall_patch.status(home, "change")["phase"] == "reviewed"


def test_gitignore_is_a_patchable_target_but_other_dot_paths_are_refused(tmp_path):
    # 2a0b24ff edits .gitignore; the gate refused every dot path, so the moving-pin manifest could not be prepared.
    assert wall_patch.target(tmp_path, ".gitignore") == tmp_path / ".gitignore"
    for name in (".git/config", ".mishe-tauftauf/state.json", ".env", "../.gitignore"):
        with pytest.raises(ValueError, match="invalid patch path"):
            wall_patch.target(tmp_path, name)


def test_review_answer_gives_the_reader_the_prompt_file_as_stdin(tmp_path):
    # claude -p reads its prompt from stdin; a regular file (not an open pipe) is the
    # stdin, so the whole prompt loads without a pipe stall.
    from mishe_tauftauf import wall_review

    seen = {}

    def runner(*args, **kwargs):
        seen["stdin"] = kwargs.get("stdin")
        return type("R", (), {"returncode": 0, "stdout": '{"version": 1, "input_hash": "h", "results": []}', "stderr": ""})()

    prompt = _req_file(tmp_path)
    wall_review.review_answer("claude-haiku-5-5", tmp_path, prompt, runner=runner)
    assert seen["stdin"].name == str(prompt)


def test_review_evidence_carries_the_modules_the_patch_imports(tmp_path, monkeypatch):
    # An unknown verdict was the default because the reviewer was shown the
    # changed file but not the modules it calls. The evidence must name them.
    home, source, deployed = layout(tmp_path)
    helper = tmp_path / "src/mishe_tauftauf/helper.py"
    helper.write_text("def nonblank(text):\n    return text.strip()\n")
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("from mishe_tauftauf.helper import nonblank\nfrom . import missing_mod\nfrom . import helper\nvalue = nonblank(' x ')\n")
    (home / "patch-review.json").write_text(json.dumps(
        {"command": [sys.executable, "-c", "pass"], "timeout_seconds": 420}))
    seen = {}

    def spy(command, encoded, timeout):
        request = json.loads(encoded.decode())
        seen["context"] = request["context"]["patch"]["context_modules"]
        return json.dumps({"version": 1, "input_hash": request["input_hash"],
                           "results": [{"id": "PATCH", "verdict": "clear", "reason": "ok", "evidence": []}]}).encode()

    monkeypatch.setattr(wall_patch, "_worker", spy)
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    context = seen["context"]
    assert list(context["modules"]) == ["mishe_tauftauf.helper:nonblank"]
    assert context["modules"]["mishe_tauftauf.helper:nonblank"]["path"] == "src/mishe_tauftauf/helper.py"
    assert "def nonblank" in context["modules"]["mishe_tauftauf.helper:nonblank"]["text"]
    assert context["unresolved"] == []
    assert wall_patch.status(home, "change")["phase"] == "reviewed"


def test_excerpt_caps_size_and_names_what_it_cuts(tmp_path):
    home, source, deployed = layout(tmp_path)
    body = "def big():\n    return '" + "a" * (wall_patch.CONTEXT_EXCERPT_BYTES + 10) + "'\n"
    (tmp_path / "src/mishe_tauftauf/big.py").write_text(body)
    source.write_text("from mishe_tauftauf.big import big\nvalue = big()\n")
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": None,
                  "after": {"bytes": __import__("base64").b64encode(source.read_bytes()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    assert context["truncated"] == ["mishe_tauftauf.big:big"]
    assert len(context["modules"]["mishe_tauftauf.big:big"]["text"]) == wall_patch.CONTEXT_EXCERPT_BYTES
    assert context["unresolved"] == []


def test_one_name_imported_by_two_patched_files_costs_one_excerpt(tmp_path, monkeypatch):
    # Two patched files importing the same name must not charge the total cap
    # twice: a single excerpt that fits the cap is complete, not omitted.
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/helper.py").write_text("def nonblank(text):\n    return text.strip()\n")
    other = tmp_path / "src/mishe_tauftauf/other.py"
    other.write_text("from mishe_tauftauf.helper import nonblank\nvalue = nonblank(' y ')\n")
    source.write_text("from mishe_tauftauf.helper import nonblank\nvalue = nonblank(' x ')\n")
    files = {name: {"before": None,
                    "after": {"bytes": base64.b64encode(path.read_bytes()).decode(), "mode": 0o644}}
             for name, path in (("src/mishe_tauftauf/example.py", source), ("src/mishe_tauftauf/other.py", other))}
    record = {"runtime": str(tmp_path / "runtime"), "files": files}
    one_copy = len("def nonblank(text):\n    return text.strip()\n")
    monkeypatch.setattr(wall_patch, "CONTEXT_TOTAL_BYTES", one_copy + 1)
    context = wall_patch.symbol_context(home, record)
    assert list(context["modules"]) == ["mishe_tauftauf.helper:nonblank"]
    assert context["omitted_over_limit"] == []


def test_changed_definition_callers_reach_the_reviewer(tmp_path):
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/other.py").write_text("from mishe_tauftauf.example import nonblank\nprint(nonblank('x'))\n")
    old = "def nonblank(text):\n    return text\n"
    new = "def nonblank(text):\n    return text.strip()\n"
    source.write_text(new)
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": {"bytes": base64.b64encode(old.encode()).decode(), "mode": 0o644},
                  "after": {"bytes": base64.b64encode(new.encode()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    assert "callers:nonblank" in context["modules"]
    assert "nonblank('x')" in context["modules"]["callers:nonblank"]["text"]
    assert context["omitted_over_limit"] == [] and context["unresolved"] == []


def test_omitted_context_marks_the_request_incomplete(tmp_path, monkeypatch):
    # An excerpt dropped over the total cap must reach the reviewer as missing
    # evidence (context_complete False), never as a complete context.
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/helper.py").write_text("def nonblank(text):\n    return text.strip()\n")
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("from mishe_tauftauf.helper import nonblank\nvalue = nonblank(' x ')\n")
    (home / "patch-review.json").write_text(json.dumps(
        {"command": [sys.executable, "-c", "pass"], "timeout_seconds": 420}))
    monkeypatch.setattr(wall_patch, "CONTEXT_TOTAL_BYTES", 10)
    seen = {}

    def spy(command, encoded, timeout):
        request = json.loads(encoded.decode())
        seen["complete"] = request["context"]["context_complete"]
        seen["omitted"] = request["context"]["patch"]["context_modules"]["omitted_over_limit"]
        return json.dumps({"version": 1, "input_hash": request["input_hash"],
                           "results": [{"id": "PATCH", "verdict": "unknown", "reason": "missing", "evidence": []}]}).encode()

    monkeypatch.setattr(wall_patch, "_worker", spy)
    with pytest.raises(ValueError, match="refused"):
        wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    assert "mishe_tauftauf.helper:nonblank" in seen["omitted"]
    assert seen["complete"] is False

def test_check_description_reaches_the_review(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    seen = {}

    def fake_review(home_, record):
        seen["rationale"] = record.get("description")
        return {"clear": False, "reason": "stop"}

    monkeypatch.setattr(wall_patch, "review", fake_review)
    with pytest.raises(ValueError, match="review"):
        wall_patch.check(home, "change", [sys.executable, "-c", "pass"], description="jitter the timers")
    assert seen["rationale"] == "jitter the timers"
    assert deployed.read_text() == "before\n"


def test_reexported_name_is_followed_to_its_definition(tmp_path):
    # `observations` re-exports a helper defined in `impl`; the reviewer must get
    # the definition, not an unresolved name.
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/impl.py").write_text("def validate_slug(text):\n    return text\n")
    (tmp_path / "src/mishe_tauftauf/facade.py").write_text("from mishe_tauftauf.impl import validate_slug\n")
    source.write_text("from mishe_tauftauf.facade import validate_slug\nvalue = validate_slug('x')\n")
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": None,
                  "after": {"bytes": __import__("base64").b64encode(source.read_bytes()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    assert context["unresolved"] == []
    assert context["modules"]["mishe_tauftauf.facade:validate_slug"]["path"] == "src/mishe_tauftauf/impl.py"
    assert "def validate_slug" in context["modules"]["mishe_tauftauf.facade:validate_slug"]["text"]


def test_annotated_module_constant_is_a_definition(tmp_path):
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/consts.py").write_text("KINDS: tuple[str, ...] = ('a', 'b')\n")
    source.write_text("from mishe_tauftauf.consts import KINDS\nvalue = KINDS\n")
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": None,
                  "after": {"bytes": __import__("base64").b64encode(source.read_bytes()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    assert context["unresolved"] == []
    assert "KINDS: tuple" in context["modules"]["mishe_tauftauf.consts:KINDS"]["text"]


def test_generic_name_callers_count_only_real_references(tmp_path):
    # A name like `run` appears in comments, strings and unrelated locals. Only
    # files importing the changed definition, and only real references, count.
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/user.py").write_text(
        "from mishe_tauftauf.example import run\n# run is mentioned in a comment\nrun()\n")
    (tmp_path / "src/mishe_tauftauf/unrelated.py").write_text("run = 3\nprint(run)\n")
    old = "def run():\n    return 1\n"
    new = "def run():\n    return 2\n"
    source.write_text(new)
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": {"bytes": base64.b64encode(old.encode()).decode(), "mode": 0o644},
                  "after": {"bytes": base64.b64encode(new.encode()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    text = context["modules"]["callers:run"]["text"]
    assert "src/mishe_tauftauf/user.py:1" in text
    assert "unrelated.py" not in text
    assert context["truncated"] == []
