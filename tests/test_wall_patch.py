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
    def failing_run(home, identity, label, command, **kwargs):
        if label == "revert":
            raise subprocess.TimeoutExpired(command, 300)
        return original(home, identity, label, command, **kwargs)
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


def test_revert_refuses_a_plan_without_revert_argv_and_moves_no_bytes(tmp_path, monkeypatch):
    # Re-running activation as a revert re-pins the new code (a5dca7c3). A record
    # whose delivery_plan has no revert argv must be refused, bytes untouched.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    wall_patch.apply(home, "change", [sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"])
    record = wall_patch.status(home, "change")
    record["delivery_plan"].pop("revert", None)
    wall_patch._save(wall_patch.location(home, "change"), record)
    with pytest.raises(ValueError, match="no revert argv"):
        wall_patch.revert(home, "change", [sys.executable, "-c", "pass"])
    assert deployed.read_text() == "after\n"
    assert wall_patch.status(home, "change")["phase"] != "reverted"


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
    wall_patch.revert(home, "change", [sys.executable, "-c", "print('restored')"])
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
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"], activate=activate, observe=observe, revert_observe=reverted, revert=[sys.executable, "-c", "pass"])
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
    wall_patch.check(home, "change", command, activate=command, observe=observe, revert_observe=reverted, revert=[sys.executable, "-c", "pass"])
    original = wall_patch.run
    def failed_observe(home_, identity, label, cmd, **kwargs):
        if label == "observe":
            return {"code": 1, "output": "boom"}
        return original(home_, identity, label, cmd, **kwargs)
    monkeypatch.setattr(wall_patch, "run", failed_observe)
    with pytest.raises(ValueError, match="observation"):
        wall_patch.apply(home, "change", command, observe)
    assert wall_patch.status(home, "change")["apply_failure"]
    monkeypatch.setattr(wall_patch, "run", original)
    wall_patch.check(home, "change", command, activate=command, observe=observe, revert_observe=reverted, revert=[sys.executable, "-c", "pass"])
    wall_patch.apply(home, "change", command, observe)
    wall_patch.verify(home, "change")
    record = wall_patch.status(home, "change")
    assert record["delivery_verified"] is True
    assert "apply_failure" not in record and "failure" not in record


def test_delivery_does_not_certify_failed_revert_observation(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    command = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", command, activate=command, observe=command,
                     revert_observe=[sys.executable, "-c", "raise SystemExit(1)"], revert=command)
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
    wall_patch.check(home, "change", command, activate=activate, observe=command, revert_observe=command, revert=[sys.executable, "-c", "pass"])
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
    wall_patch.check(home, "change", command, activate=command, observe=command, revert_observe=command, revert=[sys.executable, "-c", "pass"])
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
    wall_patch.check(home, "change", command, activate=command, observe=command, revert_observe=command, revert=[sys.executable, "-c", "pass"])
    wall_patch.apply(home, "change", command, command)
    original = wall_patch.run
    def failed_reapply(home, identity, label, command, **kwargs):
        if label == "activate":
            return original(home, identity, label, [sys.executable, "-c", "raise SystemExit(1)"], **kwargs)
        return original(home, identity, label, command, **kwargs)
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
    wall_patch.check(home, "change", command, activate=activate, observe=command, revert_observe=command, revert=[sys.executable, "-c", "pass"])
    script.write_text("raise SystemExit(1)\n")
    with pytest.raises(ValueError, match="delivery command"):
        wall_patch.apply(home, "change", activate, command)
    assert deployed.read_text() == "before\n"


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
    wall_patch.review(home, wall_patch.status(home, "change"))
    assert seen["timeout"] == 480
    assert "pre-activation review" in seen["question"]
    assert "delivery_verified" in seen["question"]
    assert "not yet expected" in seen["question"]
    assert wall_patch.status(home, "change")["phase"] == "tested"


def test_review_question_matches_the_review_path():
    # The pre-activation gate (check then review before apply) asks about a
    # delivery/recovery plan; the ordinary async review of an already-delivered
    # release asks about retained evidence. Measured cause: clean ordinary releases
    # 8ad57e3f and ac94997b drew "unknown" because they were asked for a plan the
    # ordinary path never carries.
    assert "pre-activation review" in wall_patch.review_question({})
    ordinary = wall_patch.review_question({"review_kind": "ordinary-post-delivery"})
    assert "post-delivery review" in ordinary
    assert "delivery_verified" not in ordinary


def test_review_includes_retained_recovery_evidence(tmp_path, monkeypatch):
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
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
        values["after"] = {"bytes": base64.b64encode(b"after\n").decode(), "mode": 0o644}
    record["test"] = {"log": str(tmp_path / "test.log")}
    (tmp_path / "test.log").write_text("ok\n")
    record["recovery"] = {"previous": "old", "observation_argv": ["probe"], "revert_argv": ["revert"]}
    record["review_kind"] = "ordinary-post-delivery"
    with pytest.raises(RuntimeError, match="stop after capture"):
        wall_patch.review(home, record)
    assert seen["request"]["context"]["patch"]["recovery"] == record["recovery"]
    assert "post-delivery review" in seen["request"]["questions"][0]["question"]


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
    # A non-zero exit is not an answer. The failure must name the exit code and
    # keep the stdout tail as well: when the Claude account is out of quota the
    # CLI exits 1 with "You've hit your weekly limit · resets …" on stdout and an
    # empty stderr, so a stderr-only message explained nothing (2026-11-17).
    from mishe_tauftauf import wall_review
    import importlib

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    with pytest.raises(RuntimeError, match="patch reader failed") as info:
        wall_review.review_answer("claude-haiku-5-5", tmp_path, _req_file(tmp_path),
                                  runner=lambda *a, **k:
                                  type("R", (), {"returncode": 1,
                                                 "stdout": "You've hit your weekly limit · resets Nov 18, 3am (UTC)\n",
                                                 "stderr": "boom"})())
    importlib.reload(wall_review)
    message = str(info.value)
    assert "reviewer exited 1" in message
    assert "weekly limit" in message and "boom" in message


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
    passing = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", passing)
    assert wall_patch.status(home, "change")["phase"] == "tested"

    failing = [sys.executable, "-c", "raise SystemExit(3)"]
    with pytest.raises(ValueError, match="deterministic patch check failed"):
        wall_patch.check(home, "change", failing)
    failed = wall_patch.status(home, "change")
    assert failed["phase"] == "test-failed"
    assert "review" not in failed, "a failed test must not keep the stale passing verdict"


def test_mutated_source_drops_the_stale_review_verdict(tmp_path, monkeypatch):
    # A concurrent edit to the scoped source invalidates any earlier verdict, which
    # was over different bytes; the failed phase must not render that stale verdict.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    command = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", command)
    assert wall_patch.status(home, "change")["phase"] == "tested"

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
    escaped = f"import subprocess,pathlib;pathlib.Path({str(source)!r}).write_text('peer\\n');subprocess.run(['git','-C',{str(tmp_path)!r},'-c','user.email=t@example','-c','user.name=t','add','src'],check=True);subprocess.run(['git','-C',{str(tmp_path)!r},'-c','user.email=t@example','-c','user.name=t','commit','-qm','peer'],check=True)"
    wall_patch.check(home, "change", [sys.executable, "-c", escaped])
    assert wall_patch.status(home, "change")["phase"] == "tested"


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
    wall_patch.review(home, wall_patch.status(home, "change"))
    context = seen["context"]
    assert list(context["modules"]) == ["mishe_tauftauf.helper:nonblank"]
    assert context["modules"]["mishe_tauftauf.helper:nonblank"]["path"] == "src/mishe_tauftauf/helper.py"
    assert "def nonblank" in context["modules"]["mishe_tauftauf.helper:nonblank"]["text"]
    assert context["unresolved"] == []
    assert wall_patch.status(home, "change")["phase"] == "tested"


def test_oversized_definition_keeps_header_and_names_each_omitted_run(tmp_path):
    # An oversized definition is reduced, not hard-cut: its signature and docstring
    # stay, a statement the patch does not touch becomes a one-line note with its
    # line range and byte count, and nothing is marked truncated.
    import base64
    home, source, deployed = layout(tmp_path)
    body = ('def big():\n    """Contract."""\n    return \'' + "a" * (wall_patch.CONTEXT_EXCERPT_BYTES + 10) + '\'\n')
    (tmp_path / "src/mishe_tauftauf/big.py").write_text(body)
    source.write_text("from mishe_tauftauf.big import big\nvalue = big()\n")
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": None,
                  "after": {"bytes": base64.b64encode(source.read_bytes()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    entry = context["modules"]["mishe_tauftauf.big:big"]
    assert context["truncated"] == []
    assert '"""Contract."""' in entry["text"]
    assert "omitted lines 3-3" in entry["text"]
    assert len(entry["text"]) <= wall_patch.CONTEXT_EXCERPT_BYTES
    assert context["budget"]["mishe_tauftauf.big:big"]["cut_bytes"] > 10_000
    assert context["unresolved"] == []


def test_oversized_definition_keeps_statements_that_touch_patch_names(tmp_path):
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/helper.py").write_text("def helper(x):\n    return x + 1\n")
    filler = "    _pad = '" + "a" * (wall_patch.CONTEXT_EXCERPT_BYTES + 10) + "'\n"
    body = "from mishe_tauftauf.helper import helper\n\ndef big(x):\n" + filler + "    return helper(x)\n"
    (tmp_path / "src/mishe_tauftauf/big.py").write_text(body)
    # The patch itself imports helper, so a statement naming it is one the patch uses.
    source.write_text("from mishe_tauftauf.big import big\nfrom mishe_tauftauf.helper import helper\nvalue = big(1) + helper(2)\n")
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": None,
                  "after": {"bytes": base64.b64encode(source.read_bytes()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    text = context["modules"]["mishe_tauftauf.big:big"]["text"]
    assert "return helper(x)" in text
    assert "omitted lines 2-2" in text
    assert context["truncated"] == []


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


def test_review_context_uses_the_reviewed_tree_not_head(tmp_path):
    # A post-delivery review can lag HEAD. A caller added after the snapshot
    # must not appear as evidence against that release's older patch bytes.
    import base64
    home, source, deployed = layout(tmp_path)
    release = tmp_path / "release"
    (release / "src/mishe_tauftauf").mkdir(parents=True)
    old = "def helper(text):\n    return text\n"
    new = "def helper(text):\n    return text.strip()\n"
    (release / "src/mishe_tauftauf/example.py").write_text(new)
    (release / "src/mishe_tauftauf/other.py").write_text(
        "from mishe_tauftauf.example import helper\nprint(helper('x'))\n")
    # The shared checkout at HEAD has a caller the snapshot never had.
    (tmp_path / "src/mishe_tauftauf/other.py").write_text(
        "from mishe_tauftauf.example import helper\nprint(helper('HEAD only'))\n")
    record = {"runtime": str(tmp_path / "runtime"), "sha": "0c56b9b4",
              "tree": str(release),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": {"bytes": base64.b64encode(old.encode()).decode(), "mode": 0o644},
                  "after": {"bytes": base64.b64encode(new.encode()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    text = context["modules"]["callers:helper"]["text"]
    assert "HEAD only" not in text
    assert "print(helper('x'))" in text
    assert "reviewed tree 0c56b9b4" in context["source"]


def test_missing_reviewed_tree_is_not_served_from_head(tmp_path):
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/other.py").write_text(
        "from mishe_tauftauf.example import helper\nprint(helper('HEAD only'))\n")
    record = {"runtime": str(tmp_path / "runtime"), "sha": "0c56b9b4",
              "tree": str(tmp_path / "gone"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": {"bytes": base64.b64encode(b"def helper(x):\n    return x\n").decode(), "mode": 0o644},
                  "after": {"bytes": base64.b64encode(b"def helper(x):\n    return x.strip()\n").decode(), "mode": 0o644}}}}
    with pytest.raises(ValueError, match="reviewed tree is missing"):
        wall_patch.symbol_context(home, record)

def test_removed_symbol_callers_reach_the_reviewer(tmp_path):
    # A patch that deletes a definition still in use elsewhere must show the
    # surviving caller: removal is a change, not an absence to skip.
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/other.py").write_text("from mishe_tauftauf.example import WEDGE_LIMIT\nprint(WEDGE_LIMIT)\n")
    old = "WEDGE_LIMIT = 3\n\ndef keep():\n    return 1\n"
    new = "def keep():\n    return 1\n"
    source.write_text(new)
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": {"bytes": base64.b64encode(old.encode()).decode(), "mode": 0o644},
                  "after": {"bytes": base64.b64encode(new.encode()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    assert "print(WEDGE_LIMIT)" in context["modules"]["callers:WEDGE_LIMIT"]["text"]
    assert context["unresolved"] == []


def test_corpus_parse_count_does_not_grow_with_changed_names(tmp_path, monkeypatch):
    # The caller scan used to parse the whole corpus once per changed name: a
    # release changing 231 names over a 562-file corpus parsed ~7 MB 231 times
    # and held one review unit at a core for 20 minutes (measured 2026-11-17).
    # Each corpus file is parsed and walked once before the per-name loop, so
    # the parse count is independent of how many names the patch changes.
    import base64

    def parses_for(names):
        base = tmp_path / f"names{names}"
        base.mkdir()
        home, source, deployed = layout(base)
        for i in range(8):
            (base / "src/mishe_tauftauf" / f"mod{i}.py").write_text(
                f"from mishe_tauftauf.example import g0\nvalue = g0({i})\n")
        old = "".join(f"def g{n}(x):\n    return x\n" for n in range(names))
        new = "".join(f"def g{n}(x):\n    return x + 1\n" for n in range(names))
        source.write_text(new)
        record = {"runtime": str(base / "runtime"),
                  "files": {"src/mishe_tauftauf/example.py": {
                      "before": {"bytes": base64.b64encode(old.encode()).decode(), "mode": 0o644},
                      "after": {"bytes": base64.b64encode(new.encode()).decode(), "mode": 0o644}}}}
        calls = {"n": 0}
        real = wall_patch._parsed

        def counting(text):
            calls["n"] += 1
            return real(text)

        monkeypatch.setattr(wall_patch, "_parsed", counting)
        context = wall_patch.symbol_context(home, record)
        monkeypatch.undo()
        assert "callers:g0" in context["modules"]
        return calls["n"]

    assert parses_for(40) == parses_for(5)


def test_changed_definition_callers_are_budgeted_before_imports(tmp_path, monkeypatch):
    # Imported definitions must not starve the call sites of changed definitions:
    # the caller excerpt survives, and the imported one is what gets omitted.
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/big.py").write_text("def big():\n    return '" + "a" * 150 + "'\n")
    (tmp_path / "src/mishe_tauftauf/other.py").write_text("from mishe_tauftauf.example import nonblank\nprint(nonblank('x'))\n")
    old = "def nonblank(text):\n    return text\n"
    new = "def nonblank(text):\n    return text.strip()\n"
    source.write_text(new + "from mishe_tauftauf.big import big\nvalue = big()\n")
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": {"bytes": base64.b64encode(old.encode()).decode(), "mode": 0o644},
                  "after": {"bytes": base64.b64encode(source.read_bytes()).decode(), "mode": 0o644}}}}
    monkeypatch.setattr(wall_patch, "CONTEXT_TOTAL_BYTES", 250)
    context = wall_patch.symbol_context(home, record)
    assert "callers:nonblank" in context["modules"]
    assert "mishe_tauftauf.big:big" in context["omitted_over_limit"]


def test_removed_symbol_without_callers_records_the_search(tmp_path):
    # With no surviving reference, the reviewer must see that the search ran
    # (an explicit "none found" excerpt), and the context stays complete.
    import base64
    home, source, deployed = layout(tmp_path)
    old = "WEDGE_LIMIT = 3\n\ndef keep():\n    return 1\n"
    new = "def keep():\n    return 1\n"
    source.write_text(new)
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": {"bytes": base64.b64encode(old.encode()).decode(), "mode": 0o644},
                  "after": {"bytes": base64.b64encode(new.encode()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    assert "no imported reference found" in context["modules"]["callers:WEDGE_LIMIT"]["text"]
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
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    wall_patch.review(home, wall_patch.status(home, "change"))
    assert "mishe_tauftauf.helper:nonblank" in seen["omitted"]
    assert seen["complete"] is False

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


def test_broken_dependency_module_is_named_unresolved_not_raised(tmp_path):
    # A syntactically broken module in the import closure must not escape
    # symbol_context as a traceback: the missing excerpt is named instead.
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/broken.py").write_text("def nonblank(text:\n    return\n")
    source.write_text("from mishe_tauftauf.broken import nonblank\nvalue = nonblank(' x ')\n")
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": None,
                  "after": {"bytes": base64.b64encode(source.read_bytes()).decode(), "mode": 0o644}}}}
    context = wall_patch.symbol_context(home, record)
    assert context["modules"] == {}
    assert "mishe_tauftauf.broken:nonblank" in context["unresolved"]


def test_observe_timeout_outlasts_consumer_drain(tmp_path, monkeypatch):
    # Regression: a 300 s observe kill (uncaught as TimeoutExpired) reverted a correct delivery whose
    # old consumer legitimately drained for up to DRAIN_TIMEOUT_S. The observe bound must exceed that drain.
    from mishe_tauftauf import consumer_fence
    assert wall_patch.OBSERVE_TIMEOUT_S > consumer_fence.DRAIN_TIMEOUT_S
    assert wall_patch.OBSERVE_TIMEOUT_S > wall_patch.RUN_TIMEOUT_S
    seen = {}

    def spy(command, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    monkeypatch.setattr(wall_patch.subprocess, "run", spy)
    home = tmp_path / ".mishe-tauftauf"
    (home / "patches").mkdir(parents=True)
    wall_patch.run(home, "change", "observe", ["true"], timeout=wall_patch.OBSERVE_TIMEOUT_S)
    assert seen["timeout"] == wall_patch.OBSERVE_TIMEOUT_S
    wall_patch.run(home, "change", "activate", ["true"])
    assert seen["timeout"] == wall_patch.RUN_TIMEOUT_S == 300


def test_review_context_includes_a_submodule_imported_from_the_package(tmp_path):
    # `from . import consumer_fence` names a submodule, not an export; its bytes
    # must reach the reviewer rather than being dropped without a mark.
    import base64
    from mishe_tauftauf import wall_patch

    root = tmp_path
    pkg = root / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "consumer_fence.py").write_text("DRAIN_TIMEOUT_S = 420\n")
    patched = "from . import consumer_fence\nX = consumer_fence.DRAIN_TIMEOUT_S\n"
    (pkg / "patched.py").write_text(patched)
    home = root / ".site"
    home.mkdir()
    after = {"bytes": base64.b64encode(patched.encode()).decode(), "mode": 0o644}
    record = {"files": {"src/mishe_tauftauf/patched.py": {"before": None, "after": after}}}

    context = wall_patch.symbol_context(home, record)

    assert "mishe_tauftauf.consumer_fence" in context["modules"]
    assert "DRAIN_TIMEOUT_S = 420" in context["modules"]["mishe_tauftauf.consumer_fence"]["text"]
    assert "mishe_tauftauf.consumer_fence" not in context["unresolved"]
    assert "mishe_tauftauf.consumer_fence" not in context["omitted_over_limit"]


def test_delivery_script_imports_reach_the_reviewer(tmp_path):
    # The delivery scripts are reviewed bytes too: an import they make must
    # resolve to excerpts, or the reviewer says the plan cannot be judged.
    import base64
    home, source, deployed = layout(tmp_path)
    (tmp_path / "src/mishe_tauftauf/helper.py").write_text("def nonblank(text):\n    return text.strip()\n")
    script = home / "operator/plans/delivery/activate.py"
    script.parent.mkdir(parents=True)
    script_text = "from mishe_tauftauf.helper import nonblank\nprint(nonblank(' x '))\n"
    script.write_text(script_text)
    source.write_text("before\n")
    record = {"runtime": str(tmp_path / "runtime"),
              "files": {"src/mishe_tauftauf/example.py": {
                  "before": {"bytes": base64.b64encode(b"before\n").decode(), "mode": 0o644},
                  "after": {"bytes": base64.b64encode(b"before\n").decode(), "mode": 0o644}}},
              "delivery_plan": {"artifacts": {str(script): {
                  "bytes": base64.b64encode(script_text.encode()).decode(), "mode": 0o755}}}}
    context = wall_patch.symbol_context(home, record)
    assert "mishe_tauftauf.helper:nonblank" in context["modules"]
    assert context["unresolved"] == []


def test_oversized_submodule_is_reduced_at_module_level_not_hard_cut(tmp_path):
    # `from . import discovery` names a whole submodule. Its top level (a docstring,
    # imports, a large assignment, a def) is the unit: the large assignment becomes a
    # note, the import stays, and the module is not hard-cut and marked truncated.
    import base64
    from mishe_tauftauf import wall_patch

    root = tmp_path
    pkg = root / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    big = ('"""Discovery scans."""\nimport os\nPAD = "' + "a" * (wall_patch.CONTEXT_EXCERPT_BYTES + 10)
           + '"\n\ndef scan(home):\n    return os.path.join(home, "x")\n')
    (pkg / "discovery.py").write_text(big)
    patched = "from . import discovery\nX = discovery.scan('/tmp')\n"
    (pkg / "patched.py").write_text(patched)
    home = root / ".site"
    home.mkdir()
    after = {"bytes": base64.b64encode(patched.encode()).decode(), "mode": 0o644}
    record = {"files": {"src/mishe_tauftauf/patched.py": {"before": None, "after": after}}}

    context = wall_patch.symbol_context(home, record)

    assert "mishe_tauftauf.discovery" in context["modules"]
    assert "mishe_tauftauf.discovery" not in context["truncated"]
    text = context["modules"]["mishe_tauftauf.discovery"]["text"]
    assert "import os" in text
    assert "omitted lines" in text
    assert len(text) <= wall_patch.CONTEXT_EXCERPT_BYTES


def test_oversized_module_docstring_is_named_not_filling_the_excerpt(tmp_path):
    # A long module docstring is header, not a definition: it keeps its first lines and
    # names the rest with a byte count, so the statements the patch uses still fit.
    import base64
    from mishe_tauftauf import wall_patch

    root = tmp_path
    pkg = root / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    doc = '"""Scans.\n\n' + "\n".join("doc line %d %s" % (i, "x" * 200) for i in range(400)) + '\n"""\n'
    (pkg / "discovery.py").write_text(doc + "def scan(home):\n    return home\n")
    patched = "from . import discovery\nX = discovery.scan('/tmp')\n"
    (pkg / "patched.py").write_text(patched)
    home = root / ".site"
    home.mkdir()
    after = {"bytes": base64.b64encode(patched.encode()).decode(), "mode": 0o644}
    record = {"files": {"src/mishe_tauftauf/patched.py": {"before": None, "after": after}}}

    context = wall_patch.symbol_context(home, record)

    text = context["modules"]["mishe_tauftauf.discovery"]["text"]
    assert "mishe_tauftauf.discovery" not in context["truncated"]
    assert "def scan(home):" in text
    assert "header lines" in text and "omitted" in text
    assert len(text) <= wall_patch.CONTEXT_EXCERPT_BYTES


def test_check_refuses_activation_without_explicit_revert(tmp_path, monkeypatch):
    # A plan with activate/observe/revert-observe but no revert argv would make
    # the revert re-run activate, re-pinning the new code. Refuse before any write.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    before = wall_patch.status(home, "change")
    with pytest.raises(ValueError, match="explicit revert"):
        wall_patch.check(home, "change", [sys.executable, "-c", "pass"],
                         activate=[sys.executable, "-c", "pass"],
                         observe=[sys.executable, "-c", "pass"],
                         revert_observe=[sys.executable, "-c", "pass"])
    assert wall_patch.status(home, "change") == before
    assert "delivery_plan" not in wall_patch.status(home, "change")


def test_revert_without_argv_refuses_before_bytes_move(tmp_path, monkeypatch):
    # field-review residual gap: a plan with no revert argv, reverted with
    # activate=None, used to move runtime bytes and then fail in run().
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"],
                     activate=[sys.executable, "-c", "pass"],
                     observe=[sys.executable, "-c", "pass"],
                     revert_observe=[sys.executable, "-c", "pass"],
                     revert=[sys.executable, "-c", "pass"])
    record = wall_patch.status(home, "change")
    # Simulate a plan recorded without an explicit revert argv.
    record["delivery_plan"].pop("revert")
    wall_patch._save(wall_patch.location(home, "change"), record)
    deployed_before = deployed.read_text()
    before = wall_patch.status(home, "change")
    with pytest.raises(ValueError, match="no revert argv"):
        wall_patch.revert(home, "change", None)
    assert deployed.read_text() == deployed_before
    assert wall_patch.status(home, "change") == before


def test_verify_refuses_a_plan_without_revert_argv_before_moving_bytes(tmp_path, monkeypatch):
    # a5dca7c3: verify() reverted through the new activation because the plan had
    # no revert argv, re-pinning the new code. It must refuse with bytes untouched.
    home, source, deployed = layout(tmp_path)
    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    source.write_text("after\n")
    monkeypatch.setattr(wall_patch, "review", lambda *a: {"clear": True})
    activate = [sys.executable, "-c", "pass"]
    observe = [sys.executable, "-c", "pass"]
    reverted = [sys.executable, "-c", "pass"]
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"], activate=activate, observe=observe, revert_observe=reverted, revert=[sys.executable, "-c", "pass"])
    wall_patch.apply(home, "change", activate, observe)
    record = wall_patch.status(home, "change")
    record["delivery_plan"].pop("revert", None)
    wall_patch._save(wall_patch.location(home, "change"), record)
    with pytest.raises(ValueError, match="explicit revert argv"):
        wall_patch.verify(home, "change")
    assert deployed.read_text() == "after\n"
    assert "revert_activation" not in wall_patch.status(home, "change")


def _artifact_fixture(tmp_path):
    # live checkout = tmp_path/checkout; site = checkout/.mishe-tauftauf; release tree under the site.
    checkout = tmp_path / "checkout"
    home = checkout / ".mishe-tauftauf"
    source = home / "releases" / "abc"
    (source / "tools").mkdir(parents=True)
    (checkout / "tools").mkdir(parents=True)
    (source / "tools" / "check.py").write_text("release")
    (checkout / "tools" / "check.py").write_text("checkout")
    return home, source, checkout


def test_relative_command_binds_release_script_when_run_in_release_tree(tmp_path):
    home, source, checkout = _artifact_fixture(tmp_path)
    artifacts = wall_patch.command_artifacts(home, [["python", "tools/check.py"]], cwd=source)
    assert list(artifacts) == [str((source / "tools" / "check.py").resolve())]


def test_checkout_only_change_does_not_change_release_artifacts(tmp_path):
    home, source, checkout = _artifact_fixture(tmp_path)
    before = wall_patch.command_artifacts(home, [["python", "tools/check.py"]], cwd=source)
    (checkout / "tools" / "check.py").write_text("checkout changed")
    after = wall_patch.command_artifacts(home, [["python", "tools/check.py"]], cwd=source)
    assert before == after


def test_release_script_change_is_detected(tmp_path):
    home, source, checkout = _artifact_fixture(tmp_path)
    before = wall_patch.command_artifacts(home, [["python", "tools/check.py"]], cwd=source)
    (source / "tools" / "check.py").write_text("release changed")
    after = wall_patch.command_artifacts(home, [["python", "tools/check.py"]], cwd=source)
    assert before != after


def test_absolute_owned_script_is_bound_from_checkout(tmp_path):
    home, source, checkout = _artifact_fixture(tmp_path)
    script = str((checkout / "tools" / "check.py").resolve())
    artifacts = wall_patch.command_artifacts(home, [["python", script]], cwd=source)
    assert list(artifacts) == [script]


def test_default_cwd_keeps_checkout_binding(tmp_path):
    home, source, checkout = _artifact_fixture(tmp_path)
    artifacts = wall_patch.command_artifacts(home, [["python", "tools/check.py"]])
    assert list(artifacts) == [str((checkout / "tools" / "check.py").resolve())]
