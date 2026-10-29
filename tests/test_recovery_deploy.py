import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("recovery_control", Path(__file__).parents[1] / "tools/recovery-control.py")
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


def test_renderer_wrap_restore_and_peer_change_refusal(tmp_path):
    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    top = home / "top-pains/health"
    original = b"#!/bin/sh\nprintf 'original health evidence\\n'\n"
    top.write_bytes(original)
    top.chmod(0o755)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    manifest = control.prepare_renderers(home, snapshot)
    control.wrap_renderers(home, manifest)
    assert top.read_bytes() != original
    control.restore_renderers(home, manifest)
    assert top.read_bytes() == original
    control.wrap_renderers(home, manifest)
    top.write_text("peer changed this renderer")
    with pytest.raises(ValueError, match="changed"):
        control.restore_renderers(home, manifest)
    assert top.read_text() == "peer changed this renderer"


def test_idempotent_wrapper_does_not_wrap_itself(tmp_path):
    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    top = home / "top-pains/health"
    top.write_bytes(b"#!/bin/sh\nexit 0\n")
    top.chmod(0o755)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    manifest = control.prepare_renderers(home, snapshot)
    control.wrap_renderers(home, manifest)
    first = top.read_bytes()
    control.wrap_renderers(home, manifest)
    assert top.read_bytes() == first


def test_upgrade_retains_true_original_and_accepts_only_prior_wrapper(tmp_path):
    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    top = home / "top-pains/health"
    original = b"#!/bin/sh\nexit 0\n"
    top.write_bytes(original)
    top.chmod(0o755)
    first = tmp_path / "first"
    first.mkdir()
    before = control.prepare_renderers(home, first)
    control.wrap_renderers(home, before)
    second = tmp_path / "second"
    second.mkdir()
    after = control.prepare_renderers(home, second, before)
    assert (second / "renderers-before/health").read_bytes() == original
    control.wrap_renderers(home, after)
    control.restore_renderers(home, after)
    assert top.read_bytes() == original


def test_outbox_has_independent_cold_publication_budget(tmp_path):
    text = control.units(tmp_path, tmp_path / "snapshot", "owned")["owned-recovery-outbox.service"]
    assert "MemoryMax=1536M" in text
    assert "TimeoutStartSec=60" in text
    assert "TasksMax=32" in text


def test_coverage_inventory_is_service_only_and_heals_a_poisoned_manifest():
    # A single `.timer` entry makes wall_view, substrate_watch and the health
    # pane's own check read the whole manifest as invalid, so the recovery
    # service is covered and its schedules are not. A `.timer` already present
    # must not survive the next activation.
    poisoned = ["owned-recovery.service", "owned-recovery-guard.timer"]
    enabled = ["owned-recovery.service", "owned-recovery-guard.timer",
               "owned-recovery-outbox.timer", "owned-recovery-evaluation.timer"]
    assert control.coverage_units(poisoned, enabled) == ["owned-recovery.service"]


def test_activate_writes_a_service_only_inventory(tmp_path, monkeypatch):
    # The call site, not only the helper: `activate` is the writer that poisoned
    # the live manifest, so reverting it to append every enabled name must fail
    # here rather than silently turning the health pane and the substrate
    # runtime UNKNOWN again on the next activation.
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(
        json.dumps(["owned-recovery.service", "owned-recovery-guard.timer"]) + "\n", encoding="utf-8")
    (tmp_path / ".config/systemd/user").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(control, "source_check", lambda manifest: None)
    monkeypatch.setattr(control, "wrap_renderers", lambda home, manifest: None)
    monkeypatch.setattr(control, "command", lambda argv, **kwargs: None)
    manifest = {"units": control.units(home, tmp_path / "snapshot", "owned"), "session": "owned"}
    control.activate(home, manifest)
    assert json.loads((home / "health/services.json").read_text()) == ["owned-recovery.service"]
