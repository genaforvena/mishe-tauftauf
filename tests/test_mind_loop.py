"""Per-mind desired selection and fail-closed readiness across idle cutovers."""
import os
from pathlib import Path
import subprocess

import pytest

from mishe_tauftauf import cli, mind_loop, seed
from mishe_tauftauf.feed import Feed


def script(path, body="exit 0\n", mode=0o755):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(mode)
    return path


@pytest.fixture
def site(tmp_path, monkeypatch):
    home = tmp_path / ".mishe-tauftauf"
    cli.initialize(home)
    for role in ("docs", "genome", "custom"):
        script(home / "minds" / role)
        charter = home / "charters" / f"{role}.md"
        charter.parent.mkdir(exist_ok=True)
        charter.write_text(f"Resident {role}\n")
    monkeypatch.setattr(mind_loop, "_observed_pane", lambda *a: (["100", "200"], None))
    return home


@pytest.fixture
def internal(tmp_path):
    bun = script(tmp_path / "bun")
    modules = tmp_path / "node_modules"
    for name in ("pi-ai", "pi-catalog", "pi-utils", "pi-coding-agent"):
        package = modules / "@oh-my-pi" / name / "package.json"
        package.parent.mkdir(parents=True)
        package.write_text("{}")
    return dict(kind="internal", model="provider/model", bun=bun, node_modules=modules)


def tree(home):
    return {str(path.relative_to(home)): (path.read_bytes(), path.stat().st_mode)
            for path in home.rglob("*") if path.is_file()}


def test_legacy_status_is_read_only_and_does_not_invent_running_config(site):
    before = tree(site)
    report = seed.loop(site, "custom")
    assert "desired=unknown (legacy/custom launcher" in report
    assert "running=unknown (observed legacy/unmarked pane)" in report
    assert tree(site) == before


def test_selection_is_independent_and_waits_for_rotation(site, internal, monkeypatch):
    before = (site / "minds/genome").read_bytes()
    seed.loop(site, "docs", **internal, reasoning="high", max_turns=2)
    desired, meta = mind_loop._current(site, "docs")
    assert meta["config"]["reasoning"] == "high"
    assert (site / "minds/genome").read_bytes() == before
    assert "desired=internal" in seed.loop(site, "docs")
    assert "running=unknown" in seed.loop(site, "docs")
    monkeypatch.setattr(mind_loop, "_observed_pane", lambda *a: (["101", "201"], str(desired)))
    assert f"running=internal model=provider/model receipt={desired}" in seed.loop(site, "docs")


@pytest.mark.parametrize("change", [
    {"model": "missing-provider"}, {"max_turns": 0}, {"max_calls": -1},
    {"timeout": float("nan")}, {"bun": Path("relative-bun")},
    {"reasoning": "unsupported"}, {"node_modules": Path("/nonexistent/dependencies")},
])
def test_invalid_internal_config_has_no_writes(site, internal, change):
    before = tree(site)
    with pytest.raises(ValueError):
        seed.loop(site, "docs", **(internal | change))
    assert tree(site) == before
    assert not (site / ".seed.lock").exists()


@pytest.mark.parametrize("kind", ["internal", "external"])
def test_unavailable_pinned_runtime_is_rejected_before_writes(site, internal, tmp_path, monkeypatch, kind):
    from mishe_tauftauf import runtime_source
    selected = tmp_path / "old-release/src"
    selected.mkdir(parents=True)
    monkeypatch.setattr(runtime_source, "pane_python_path", lambda *a: str(selected))
    before = tree(site)
    with pytest.raises(ValueError, match="selected runtime source.*deploy"):
        options = internal if kind == "internal" else {"kind": "external", "engine_command": "/bin/true"}
        seed.loop(site, "docs", **options)
    assert tree(site) == before


@pytest.mark.parametrize("held", ["pending", "busy"])
def test_pending_and_native_busy_hold_without_changing_launcher_or_receipts(site, internal, held):
    if held == "pending":
        Feed(site).append("seed", "seed wake docs observation=1\nRead the current dashboard.")
    else:
        busy = site / "journals/docs-native.busy"
        busy.parent.mkdir()
        busy.write_text("owned active wake")
    before = (site / "minds/docs").read_bytes()
    with pytest.raises(ValueError, match="pending wake|native busy"):
        seed.loop(site, "docs", **internal)
    assert (site / "minds/docs").read_bytes() == before
    assert not (site / ".mishe-loop").exists()


def test_external_command_is_validated_and_explicit_argv_not_shell(site, tmp_path):
    before = tree(site)
    with pytest.raises(ValueError, match="executable"):
        seed.loop(site, "docs", kind="external", engine_command="definitely-not-installed-loop-command")
    assert tree(site) == before
    output = tmp_path / "argv"
    program = script(tmp_path / "external", 'printf "%s\\n" "$@" > "$OUTPUT"\n')
    seed.loop(site, "custom", kind="external", engine_command=f"'{program}' 'literal ; arg'")
    subprocess.run([str(site / "minds/custom")], env=os.environ | {"OUTPUT": str(output)}, check=True)
    assert output.read_text() == "literal ; arg\n"


def test_internal_then_external_restores_custom_gate_behavior(site, internal):
    gate = script(site / "checks/mind-ready/docs", "exit 0\n")
    original = gate.read_bytes()
    seed.loop(site, "docs", **internal)
    assert mind_loop.ready(site, "docs")  # Old unmarked custom process retains gate.
    seed.loop(site, "docs", kind="external", engine_command="/bin/true")
    directory, meta = mind_loop._current(site, "docs")
    assert (directory / "probe").read_bytes() == original
    assert meta["probe"]["mode"] == 0o755


def test_exact_rollback_retains_runtime_guard_for_old_marked_process(site, internal, monkeypatch):
    launcher = site / "minds/docs"
    launcher.chmod(0o751)
    original = launcher.read_bytes()
    script(site / "checks/mind-ready/docs", "exit 0\n")
    seed.loop(site, "docs", **internal)
    first, _ = mind_loop._current(site, "docs")
    # A different role's valid immutable receipt is not rollback authority here.
    script(site / "checks/mind-ready/custom", "exit 1\n")
    seed.loop(site, "custom", kind="external", engine_command="/bin/true")
    custom_receipt, _ = mind_loop._current(site, "custom")
    with pytest.raises(ValueError, match="this home and role"):
        seed.loop(site, "docs", restore=str(custom_receipt))
    report = seed.loop(site, "docs", restore=str(first))
    assert launcher.read_bytes() == original
    assert launcher.stat().st_mode & 0o777 == 0o751
    assert "probe_dispatcher_retained=true" in report
    # A marked internal process continues using native --ready, not legacy gate.
    monkeypatch.setattr(mind_loop, "_observed_pane", lambda *a: (["101", "201"], str(first)))
    assert not mind_loop.ready(site, "docs")
    # After actual rotation, the exact unmarked legacy launcher regains its gate.
    monkeypatch.setattr(mind_loop, "_observed_pane", lambda *a: (["102", "202"], None))
    assert mind_loop.ready(site, "docs")


def test_readiness_rejects_missing_proc_identity_and_foreign_receipt(site, internal, monkeypatch):
    script(site / "checks/mind-ready/docs", "exit 0\n")
    seed.loop(site, "docs", **internal)
    def unreadable(*args):
        raise PermissionError("proc unavailable")
    monkeypatch.setattr(mind_loop, "_observed_pane", unreadable)
    assert not mind_loop.ready(site, "docs")
    monkeypatch.setattr(mind_loop, "_observed_pane", lambda *a: (["100", "200"], "/tmp/arbitrary-probe"))
    assert not mind_loop.ready(site, "docs")



def test_restored_legacy_launcher_cannot_inherit_calling_resident_receipt(site, monkeypatch):
    launcher = script(site / "minds/docs", 'printf "%s\\n" "${MISHE_LOOP_RECEIPT-unmarked}"\n')
    original = launcher.read_bytes()
    seed.loop(site, "docs", kind="external", engine_command="/bin/true")
    receipt, _ = mind_loop._current(site, "docs")
    seed.loop(site, "docs", restore=str(receipt))
    assert launcher.read_bytes() == original
    monkeypatch.setenv("MISHE_LOOP_RECEIPT", "/other-site/.mishe-loop/genome/stale")
    argv = seed._mind_launch_argv(site, "docs")
    result = subprocess.run(argv[2:], cwd=argv[1], capture_output=True, text=True, check=True)
    assert result.stdout == "unmarked\n"


def test_preserved_custom_probe_keeps_sibling_resource_resolution(site, internal, monkeypatch):
    script(site / "checks/mind-ready/helper", "exit 0\n")
    script(site / "checks/mind-ready/docs", 'exec "$(dirname "$0")/helper"\n')
    seed.loop(site, "docs", **internal)
    assert mind_loop.ready(site, "docs")  # Original still-running legacy pane.
    seed.loop(site, "docs", kind="external", engine_command="/bin/true")
    external, _ = mind_loop._current(site, "docs")
    monkeypatch.setattr(mind_loop, "_observed_pane", lambda *a: (["101", "201"], str(external)))
    assert mind_loop.ready(site, "docs")  # New marked external uses preserved gate.
    alias = mind_loop._probe_alias(site, "docs", external, "probe")
    alias.unlink()
    script(alias, "exit 0\n")
    assert not mind_loop.ready(site, "docs")  # A substituted adjacent file is not provenance.
