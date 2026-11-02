"""Understanding gate (Bacchelli & Bird ICSE 2013 §VI) — prompt and request carry rationale.

The reviewer prompt must force `unknown` with "insufficient context to assess
deeper defects" when context is insufficient, not a shallow `clear`. The gate
is proxied for the LLM by the supplied rationale (`description`/`rationale` in
the patch record) via wall_patch.review evidence["rationale"].
"""
import json
import hashlib
import base64
from pathlib import Path
from unittest import mock


def test_wall_review_prompt_contains_understanding_gate():
    text = Path("src/mishe_tauftauf/wall_review.py").read_text(encoding="utf-8")
    assert "sufficient context to judge the rationale and impact" in text
    assert "insufficient context to assess deeper defects" in text
    assert "Do not invent rationale" in text


def _prepare_with_after(tmp_path, description=None, rationale=None):
    from mishe_tauftauf import wall_patch
    from mishe_tauftauf.wall_patch import _save, location, snapshot, target

    home = tmp_path / "site"
    home.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "runtime": str(runtime)}))
    src = tmp_path / "src/mishe_tauftauf"
    src.mkdir(parents=True)
    (src / "example.py").write_text("before\n")
    (runtime / "src/mishe_tauftauf").mkdir(parents=True)
    (runtime / "src/mishe_tauftauf/example.py").write_text("before\n")

    wall_patch.prepare(home, "change", ["src/mishe_tauftauf/example.py"])
    src_file = tmp_path / "src/mishe_tauftauf/example.py"
    src_file.write_text("after\n")

    record = wall_patch.status(home, "change")
    # Fill `after` as check() would
    name = "src/mishe_tauftauf/example.py"
    record["files"][name]["after"] = snapshot(target(home.parent, name))
    if description is not None:
        record["description"] = description
    if rationale is not None:
        record["rationale"] = rationale
    # review() needs test artefact
    test_log = tmp_path / "test.log"
    test_log.write_text("ok\n")
    record["test"] = {"code": 0, "log": str(test_log)}
    record["delivery_plan"] = wall_patch.delivery_plan(home, ["true"], ["true"])
    _save(location(home, "change"), record)
    (home / "publication-check.json").write_text(json.dumps({"command": ["true"], "timeout_seconds": 42}))
    return home


def test_wall_patch_review_includes_rationale_when_present(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_patch

    home = _prepare_with_after(tmp_path, description="fix off-by-one in wall skeleton heading check")

    captured = {}

    def fake_worker(config, payload, timeout):
        req = json.loads(payload.decode())
        captured["request"] = req
        return json.dumps({
            "version": 1,
            "input_hash": req["input_hash"],
            "results": [{"id": "PATCH", "verdict": "clear", "reason": "ok", "evidence": ["exact scoped patch bytes and test result"]}],
        }).encode()

    monkeypatch.setattr(wall_patch, "_worker", fake_worker)

    result = wall_patch.review(home, wall_patch.status(home, "change"))
    assert result["clear"] is True
    assert "rationale" in captured["request"]["context"]["patch"]
    assert captured["request"]["context"]["patch"]["rationale"] == "fix off-by-one in wall skeleton heading check"


def test_wall_patch_review_excludes_rationale_when_absent(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_patch

    home = _prepare_with_after(tmp_path)

    captured = {}

    def fake_worker(config, payload, timeout):
        req = json.loads(payload.decode())
        captured["request"] = req
        return json.dumps({
            "version": 1,
            "input_hash": req["input_hash"],
            "results": [{"id": "PATCH", "verdict": "clear", "reason": "ok", "evidence": ["exact scoped patch bytes and test result"]}],
        }).encode()

    monkeypatch.setattr(wall_patch, "_worker", fake_worker)

    wall_patch.review(home, wall_patch.status(home, "change"))
    assert "rationale" not in captured["request"]["context"]["patch"]


def test_wall_patch_review_prefers_description_over_rationale_field(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_patch

    home = _prepare_with_after(tmp_path, description="desc first", rationale="rationale second")

    captured = {}

    def fake_worker(config, payload, timeout):
        captured["request"] = json.loads(payload.decode())
        return json.dumps({
            "version": 1,
            "input_hash": captured["request"]["input_hash"],
            "results": [{"id": "PATCH", "verdict": "clear", "reason": "ok", "evidence": ["exact scoped patch bytes and test result"]}],
        }).encode()

    monkeypatch.setattr(wall_patch, "_worker", fake_worker)

    wall_patch.review(home, wall_patch.status(home, "change"))
    assert captured["request"]["context"]["patch"]["rationale"] == "desc first"
