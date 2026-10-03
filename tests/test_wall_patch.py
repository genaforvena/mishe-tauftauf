import json
import sys
import pytest
from mishe_tauftauf import wall_patch


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
        return json.dumps({"version": 1, "input_hash": request["input_hash"],
                           "results": [{"id": "PATCH", "verdict": "clear", "reason": "ok", "evidence": []}]}).encode()

    monkeypatch.setattr(wall_patch, "_worker", spy)
    wall_patch.check(home, "change", [sys.executable, "-c", "pass"])
    assert seen["timeout"] == 480
    assert wall_patch.status(home, "change")["phase"] == "reviewed"
