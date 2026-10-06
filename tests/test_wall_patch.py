import json
import sys
import pytest
from mishe_tauftauf import wall_patch
import subprocess


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
    assert wall_patch.status(home, "change")["phase"] == "test-mutated-source"


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
    assert record["revert_observation"]["code"] == 0
    assert record["verification"]["patch_hash"]
    assert deployed.read_text() == "after\n"


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
    assert wall_review.reviewer_model(home) == wall_review.DEFAULT_MODEL
    (home / "patch-review.json").write_text(json.dumps({"model": "nvidia/moonshotai/kimi-k3"}))
    assert wall_review.reviewer_model(home) == "nvidia/moonshotai/kimi-k3"
    monkeypatch.setenv("MISHE_WALL_REVIEW_MODEL", "override/model")
    assert wall_review.reviewer_model(home) == "override/model"


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
    answer = wall_review.review_answer("model", tmp_path, tmp_path / "req.txt", runner=empty_then_ok)
    importlib.reload(wall_review)
    assert len(runs) == 3, "empty output must be retried, not accepted on the empty run"
    assert answer == json.loads(good)


def test_empty_reviewer_output_names_the_failure_when_retries_exhaust(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_review
    import importlib

    monkeypatch.setenv("MISHE_WALL_REVIEW_TIMEOUT", "120")
    importlib.reload(wall_review)
    with pytest.raises(RuntimeError, match="patch reader returned empty output"):
        wall_review.review_answer("model", tmp_path, tmp_path / "req.txt",
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
        wall_review.review_answer("model", tmp_path, tmp_path / "req.txt",
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
    answer = wall_review.review_answer("model", tmp_path, tmp_path / "req.txt",
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
        wall_review.review_answer("model", tmp_path, tmp_path / "req.txt",
                                  runner=lambda *a, **k:
                                  type("R", (), {"returncode": 0,
                                                 "stdout": '{"version": 1}', "stderr": ""})(),
                                  validate=reject)
    importlib.reload(wall_review)


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
