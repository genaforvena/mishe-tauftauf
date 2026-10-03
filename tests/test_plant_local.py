from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from mishe_tauftauf import plant


def test_refresh_contract_preserves_other_agent_instructions() -> None:
    module = vars(plant)
    refresh = module["refresh_contract"]
    old = "User instruction.\n\n<!-- mishe-tauftauf plant contract -->\nOld rule.\n<!-- end mishe-tauftauf plant contract -->\n\nFinal instruction.\n"
    new = "<!-- mishe-tauftauf plant contract -->\nNew rule.\n"
    result = refresh(old, new)
    assert "User instruction." in result
    assert "Final instruction." in result
    assert "New rule." in result
    assert "Old rule." not in result
    assert refresh(result, new) == result


def test_linked_systemd_fragment_matches_site_unit(tmp_path: Path) -> None:
    module = vars(plant)
    unit = tmp_path / "site" / "mishe-example-genome.service"
    unit.parent.mkdir()
    unit.write_text("[Unit]\n")
    linked = tmp_path / "systemd" / unit.name
    linked.parent.mkdir()
    linked.symlink_to(unit)
    assert module["unit_fragment_matches"](str(linked), unit)
    assert not module["unit_fragment_matches"](str(tmp_path / "other.service"), unit)

def test_fragment_mismatch_names_a_drifted_regular_file(tmp_path: Path) -> None:
    # An out-of-band hand edit that copies unit content into the systemd path
    # replaces the symlink shape the plant installs, and the old "already belongs
    # to" refusal reads like a write conflict rather than the shape defect it is.
    mismatch = plant.unit_fragment_mismatch
    unit = tmp_path / "site" / "mishe-example-silence.service"
    unit.parent.mkdir()
    unit.write_text("[Unit]\n")
    # systemd's FragmentPath resolves symlinks, so a resolving symlink agrees.
    linked = tmp_path / "systemd" / unit.name
    linked.parent.mkdir()
    linked.symlink_to(unit)
    assert mismatch(str(linked), unit) is None
    # A regular file at the systemd path, byte-identical or not, is the drift.
    linked.unlink()
    linked.write_text(unit.read_text())
    assert mismatch(str(linked), unit) == "regular file instead of a symlink to the site unit"
    # A symlink to anywhere else is a foreign link, not a shape defect.
    other = tmp_path / "elsewhere" / unit.name
    other.parent.mkdir()
    other.write_text("[Unit]\n")
    linked.write_text("")
    linked.unlink()
    linked.symlink_to(other)
    assert mismatch(str(linked), unit) == f"symlink points at {other} instead of the site unit"
    # An absent fragment means systemd has not linked the unit yet.
    assert mismatch("", unit) is None


def test_silence_unit_follows_the_pinned_release(tmp_path: Path) -> None:
    from mishe_tauftauf.runtime_source import select_source

    workspace = tmp_path / "repo"
    package = workspace / "src" / "mishe_tauftauf"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(workspace), "-c", "user.name=test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "release"], check=True)
    sha = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
    release = tmp_path / "release"
    subprocess.run(["git", "-C", str(workspace), "worktree", "add", "--detach", str(release), sha],
                   check=True, capture_output=True)
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    select_source(home, release, "core")
    text = plant.silence_unit_text(home, "core", "/usr/bin/python3")
    assert "mishe_tauftauf.activity --home " in text
    assert "--session core --interval 5" in text
    assert f"Environment=PYTHONPATH={release / 'src'}" in text
    # The watcher does not take a pane, so it restarts on failure only.
    assert "Restart=on-failure" in text
    assert "Restart=always" not in text


def test_replant_keeps_existing_operator_window_name(tmp_path: Path) -> None:
    module = vars(plant)
    site = tmp_path / "site"
    (site / "health").mkdir(parents=True)
    (site / "health" / "windows.json").write_text(
        '["codex", "discover", "genome", "health", "permissions", "senses", "witness"]\n'
    )
    choose = module["preferred_operator_window"]
    assert choose(site, None) == "codex"
    assert choose(site, "codex") == "codex"
    with pytest.raises(ValueError, match="site already uses operator window"):
        choose(site, "different")
    assert choose(tmp_path / "fresh", None) == "operator"


def test_default_plant_reuses_only_existing_resident_site(tmp_path: Path) -> None:
    module = vars(plant)
    choose = module["site_and_session"]
    assert choose(tmp_path, ".mishe-seed", None, None) == (tmp_path / ".mishe-seed", "mishe-seed")
    existing = tmp_path / ".mishe-tauftauf"
    existing.mkdir()
    (existing / ".seed-raised").write_text("mishe-self-development-current $391\n")
    assert choose(tmp_path, ".mishe-seed", None, None) == (existing, "mishe-self-development-current")
    other = tmp_path / ".mishe-seed"
    other.mkdir()
    (other / ".seed-raised").write_text("mishe-seed $392\n")
    with pytest.raises(ValueError, match="multiple resident sites"):
        choose(tmp_path, ".mishe-seed", None, None)


def test_planted_roles_include_every_chartered_mind() -> None:
    # docs is chartered (seed_docs_charter.md), wall-addressable and listed in
    # activity.ROLES; it must be a planted role so a reboot recreates its window
    # from a service instead of relying on an out-of-band respawn.
    assert "docs" in plant.ROLES


def test_replant_preserves_existing_custom_engine_without_default_binary(tmp_path: Path) -> None:
    home = tmp_path / ".mishe-tauftauf"
    minds = home / "minds"
    minds.mkdir(parents=True)
    with pytest.raises(ValueError, match="agent command unavailable"):
        plant.ensure_engine_for_new_minds(home, "missing-engine-for-test")
    for slug in plant.ROLES:
        (minds / slug).write_text("#!/bin/sh\nexec custom-engine\n", encoding="utf-8")
    plant.ensure_engine_for_new_minds(home, "missing-engine-for-test")


def test_normal_replant_keeps_installed_immutable_source(tmp_path):
    import json
    import subprocess
    from mishe_tauftauf import seed

    release = tmp_path / "release"
    package = release / "src/mishe_tauftauf"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "seed_doctrine.md").write_text("Reviewed frozen doctrine.\n")
    (package / "seed_discover_charter.md").write_text("Reviewed frozen discovery charter.\n")
    subprocess.run(["git", "-C", str(release), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(release), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(release), "-c", "user.name=test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "immutable runtime"], check=True)
    sha = subprocess.check_output(["git", "-C", str(release), "rev-parse", "HEAD"], text=True).strip()
    home = tmp_path / "application/.mishe-tauftauf"
    (home / "health").mkdir(parents=True)
    (home / "health/runtime-release.json").write_text(json.dumps({
        "version": 1, "source": str(release), "sha": sha, "session": "owned"}))
    assert f"Environment=PYTHONPATH={release / 'src'}" in plant.unit_text(home, "owned", "genome", "python3")
    argv = seed._mind_launch_argv(home, "genome")
    assert any(arg.startswith(f"PYTHONPATH={release / 'src'}") for arg in argv)
    assert seed._core_doctrine(home) == "Reviewed frozen doctrine.\n"
    assert seed._core_charter("discover", home) == "Reviewed frozen discovery charter.\n"
    from mishe_tauftauf.runtime_source import refresh_cli
    cli = home / "bin/mishe-tauftauf"
    cli.parent.mkdir()
    cli.write_text('#!/bin/sh\nexport PYTHONPATH=/old/src${PYTHONPATH:+:$PYTHONPATH}\nexec /usr/bin/python3 -m mishe_tauftauf "$@"\n')
    cli.chmod(0o755)
    refresh_cli(home, release)
    assert str(release / "src") in cli.read_text()
    assert cli.stat().st_mode & 0o111
    # A corrupt or changed pin must fail instead of silently returning to mutable code.
    (package / "__init__.py").write_text("changed")
    with pytest.raises(ValueError, match="runtime.*clean|runtime.*changed"):
        plant.unit_text(home, "owned", "genome", "python3")


def test_runtime_source_rejects_whitespace_before_pin_write(tmp_path):
    from mishe_tauftauf.runtime_source import select_source
    home = tmp_path / "site"
    with pytest.raises(ValueError, match="whitespace"):
        select_source(home, tmp_path / "release with spaces", "owned")
    assert not (home / "health/runtime-release.json").exists()


def test_service_manifest_lists_every_planted_unit(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    # `silence` is generated now, so a fresh site's manifest names it without
    # waiting for an out-of-band install to append it.
    assert plant.service_manifest(home, "core", persist=True) == sorted(
        plant.unit_name("core", slug) for slug in (*plant.ROLES, "permissions", "ci", *plant.OUT_OF_BAND))


def test_replant_keeps_installed_out_of_band_units_in_the_manifest(tmp_path: Path) -> None:
    # `coordination` still installs itself out of band and appends itself here;
    # the plant must not overwrite the file, or a dead one reads GREEN and silent
    # on the dashboard. `silence` is generated now, so it is present either way.
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    manifest = home / "health" / "services.json"
    manifest.write_text(json.dumps([
        "core-ci.service", "core-genome.service",
        "core-coordination.service", "core-silence.service",
    ]) + "\n", encoding="utf-8")
    names = plant.service_manifest(home, "core", persist=True)
    assert set(names) >= {"core-coordination.service", "core-silence.service"}
    assert plant.unit_name("core", "genome") in names


def test_replant_drops_units_belonging_to_another_session(tmp_path: Path) -> None:
    # Only this session's units are carried over; a stale unit from an earlier
    # session name would otherwise stay in the coverage set forever.
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    manifest = home / "health" / "services.json"
    manifest.write_text(json.dumps(["other-session-silence.service"]) + "\n", encoding="utf-8")
    assert "other-session-silence.service" not in plant.service_manifest(home, "core", persist=True)


def test_service_manifest_starts_empty_without_persist(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text('["core-ci.service"]\n', encoding="utf-8")
    assert plant.service_manifest(home, "core", persist=False) == []


def test_plant_without_services_preserves_an_existing_manifest(tmp_path: Path) -> None:
    # A `--no-services` plant installs nothing, so it must keep recording the
    # site's units; an emptied manifest reads RED and hides a genuinely dead unit.
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    manifest = home / "health" / "services.json"
    manifest.write_text(json.dumps(["core-ci.service"]) + "\n", encoding="utf-8")
    assert plant.write_service_manifest(home, "core", persist=False) == []
    assert json.loads(manifest.read_text(encoding="utf-8")) == ["core-ci.service"]


def test_plant_without_services_records_an_empty_manifest_for_a_fresh_site(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    plant.write_service_manifest(home, "core", persist=False)
    assert json.loads((home / "health" / "services.json").read_text(encoding="utf-8")) == []


def test_service_manifest_ignores_a_corrupt_manifest(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text("{not json", encoding="utf-8")
    assert plant.unit_name("core", "ci") in plant.service_manifest(home, "core", persist=True)


def test_python_search_path_keeps_order_and_collapses_duplicates() -> None:
    from mishe_tauftauf.runtime_source import python_search_path

    assert python_search_path("/a", "/a") == "/a"
    assert python_search_path("/a", "/a:/b") == f"/a{os.pathsep}/b"
    assert python_search_path("/a:/b", "/b:/c") == f"/a{os.pathsep}/b{os.pathsep}/c"
    assert python_search_path(None, "") == ""


def test_python_command_does_not_repeat_the_package_root(monkeypatch) -> None:
    from mishe_tauftauf import tmux

    root = str(Path(tmux.__file__).resolve().parents[1])
    monkeypatch.setenv("PYTHONPATH", root)
    command = tmux._python_command("--home", "site", "doctor")
    assert f"PYTHONPATH={root}" in command


def test_runtime_only_cli_preserves_dirty_application_and_contract(tmp_path: Path) -> None:
    import subprocess
    import sys
    import uuid

    workspace = tmp_path / "application"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    home = workspace / ".mishe-tauftauf"
    session = "mishe-refresh-test-" + uuid.uuid4().hex[:10]
    argv = [sys.executable, "-m", "mishe_tauftauf.plant", "--workspace", str(workspace),
            "--home", str(home), "--session", session, "--engine-command", "cat", "--no-services"]
    try:
        initial = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        assert initial.returncode == 0, initial.stderr
        agents = workspace / "AGENTS.md"
        agents.write_text("Unlanded application contract.\n")
        application = workspace / "application.py"
        application.write_text("unlanded application work\n")
        mind = home / "minds/genome"
        original_mind = mind.read_bytes()
        refresh = subprocess.run([*argv, "--runtime-only"], capture_output=True, text=True, timeout=45)
        assert refresh.returncode == 0, refresh.stderr
        assert agents.read_text() == "Unlanded application contract.\n"
        assert application.read_text() == "unlanded application work\n"
        assert mind.read_bytes() == original_mind
        # Select an independent committed package and exercise the real refresh caller.
        import shutil
        release = tmp_path / "release"
        shutil.copytree(Path(plant.__file__).parents[1], release / "src", ignore=shutil.ignore_patterns("__pycache__"))
        subprocess.run(["git", "-C", str(release), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(release), "add", "src"], check=True)
        subprocess.run(["git", "-C", str(release), "-c", "user.name=test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "runtime snapshot"], check=True)
        selected = subprocess.run([*argv, "--runtime-only", "--runtime-source", str(release)],
                                  capture_output=True, text=True, timeout=45)
        assert selected.returncode == 0, selected.stderr
        assert str(release / "src") in (home / "bin/mishe-tauftauf").read_text()
        assert agents.read_text() == "Unlanded application contract.\n"
        assert application.read_text() == "unlanded application work\n"
        assert mind.read_bytes() == original_mind
        pid = subprocess.check_output(["tmux", "display-message", "-p", "-t", f"{session}:genome.0", "#{pane_pid}"],
                                      text=True).strip()
        assert f"PYTHONPATH={release / 'src'}".encode() in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    finally:
        subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                        "seed", "stop", "--session", session], capture_output=True, timeout=15)


def test_runtime_refresh_does_not_wait_for_delivery_projection(tmp_path):
    import subprocess
    import sys
    import uuid
    workspace = tmp_path / "application"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    home = workspace / ".mishe-tauftauf"
    session = "mishe-maintenance-test-" + uuid.uuid4().hex[:10]
    script = """
from mishe_tauftauf import delivery, ci_watch
import runpy

def unavailable_maintenance(home):
    raise RuntimeError('delivery projection may wait indefinitely for configured model admission')
delivery.check_all = unavailable_maintenance
ci_watch.read = lambda home: dict(state='unknown', sha='unknown', run='none', url='none', detail='isolated CI fixture')
runpy.run_module('mishe_tauftauf.plant', run_name='__main__')
"""
    try:
        initial_script = script.replace("delivery.check_all = unavailable_maintenance", "delivery.check_all = lambda home: None")
        initial = subprocess.run([sys.executable, "-c", initial_script, "--workspace", str(workspace),
            "--home", str(home), "--session", session, "--engine-command", "cat", "--no-services"],
            capture_output=True, text=True, timeout=45)
        assert initial.returncode == 0, initial.stderr
        result = subprocess.run([sys.executable, "-c", script, "--workspace", str(workspace),
            "--home", str(home), "--session", session, "--engine-command", "cat", "--no-services",
            "--runtime-only"], capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr
        assert "plant ready" in result.stdout
        from mishe_tauftauf import ci_watch
        assert ci_watch.latest(home)["state"] == "unknown"
    finally:
        subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                        "seed", "stop", "--session", session], capture_output=True, timeout=15)


def test_site_declared_roles_finds_chartered_residents(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "charters").mkdir(parents=True)
    (home / "minds").mkdir(parents=True)
    (home / "charters" / "body-research.md").write_text("# Body research\n", encoding="utf-8")
    (home / "minds" / "body-research").write_text("#!/bin/sh\nexec omp\n", encoding="utf-8")
    assert plant.site_declared_roles(home) == {"body-research"}


def test_site_declared_roles_requires_charter_and_launcher(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "charters").mkdir(parents=True)
    (home / "minds").mkdir(parents=True)
    # Charter without launcher is not a declared resident.
    (home / "charters" / "ghost.md").write_text("# Ghost\n", encoding="utf-8")
    # Launcher without charter is not a declared resident.
    (home / "minds" / "orphan").write_text("#!/bin/sh\nexec omp\n", encoding="utf-8")
    assert plant.site_declared_roles(home) == set()


def test_site_declared_roles_empty_for_fresh_site(tmp_path: Path) -> None:
    home = tmp_path / "site"
    assert plant.site_declared_roles(home) == set()


def test_preferred_operator_window_ignores_site_declared_roles(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "charters").mkdir(parents=True)
    (home / "minds").mkdir(parents=True)
    (home / "charters" / "body-research.md").write_text("# Body research\n", encoding="utf-8")
    (home / "minds" / "body-research").write_text("#!/bin/sh\nexec omp\n", encoding="utf-8")
    (home / "health" / "windows.json").write_text(
        json.dumps(["operator", "body-research", "genome", "health"]) + "\n", encoding="utf-8"
    )
    assert plant.preferred_operator_window(home, None) == "operator"
    assert plant.preferred_operator_window(home, "operator") == "operator"
    with pytest.raises(ValueError, match="site already uses operator window"):
        plant.preferred_operator_window(home, "different")


def test_manifest_windows_reads_existing(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(
        json.dumps(["operator", "body-research", "genome"]) + "\n", encoding="utf-8"
    )
    assert plant._manifest_windows(home) == ["operator", "body-research", "genome"]


def test_manifest_windows_empty_when_missing(tmp_path: Path) -> None:
    home = tmp_path / "site"
    assert plant._manifest_windows(home) == []


def test_manifest_windows_empty_when_corrupt(tmp_path: Path) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text("{not json", encoding="utf-8")
    assert plant._manifest_windows(home) == []


def _fake_systemctl(monkeypatch, fragments: dict[str, Path]) -> list[list[str]]:
    """Replace the plant's systemctl runner and record the calls it makes."""
    calls: list[list[str]] = []

    class Runner:
        def run(self, argv, **kwargs):
            calls.append(list(argv))
            stdout = ""
            if argv[:3] == ["systemctl", "--user", "show"]:
                fragment = fragments.get(argv[3])
                stdout = f"{fragment}\n" if fragment is not None else "\n"
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(plant, "subprocess", Runner())
    monkeypatch.setattr(plant, "_service_active", lambda name, env: False)
    return calls


def test_repoint_release_root_moves_only_the_source_entry(tmp_path: Path) -> None:
    pinned = tmp_path / "releases/new/src"
    unit = (
        "[Unit]\nDescription=Mishe coordination\n\n[Service]\nType=simple\n"
        f"Environment=PYTHONPATH={tmp_path}/releases/old/src\n"
        "Environment=PATH=/site/bin:/usr/bin\n"
        "ExecStart=/site/bin/coordination --follow\nRestart=always\n"
    )
    updated = plant.repoint_release_root(unit, pinned)
    assert f"Environment=PYTHONPATH={pinned}\n" in updated
    assert "releases/old" not in updated
    assert "Environment=PATH=/site/bin:/usr/bin\n" in updated
    assert "ExecStart=/site/bin/coordination --follow\n" in updated
    assert "Restart=always\n" in updated


def test_repoint_release_root_keeps_extra_search_path_entries(tmp_path: Path) -> None:
    pinned = tmp_path / "releases/new/src"
    unit = f"Environment=PYTHONPATH={tmp_path}/releases/old/src:/opt/extra/lib\n"
    assert plant.repoint_release_root(unit, pinned) == f"Environment=PYTHONPATH={pinned}:/opt/extra/lib\n"


def test_repoint_release_root_keeps_an_unrelated_source_entry(tmp_path: Path) -> None:
    # Only entries inside this site's ``releases`` directory are the pin; a
    # custom unit may legitimately add its own ``/src`` search path.
    pinned = tmp_path / "releases/new/src"
    unit = f"Environment=PYTHONPATH={tmp_path}/releases/old/src:/opt/vendor/src\n"
    assert plant.repoint_release_root(unit, pinned) == f"Environment=PYTHONPATH={pinned}:/opt/vendor/src\n"


def test_repoint_release_root_leaves_other_lines_alone(tmp_path: Path) -> None:
    unit = "[Service]\nEnvironment=PATH=/site/bin:/usr/bin\nExecStart=/bin/true\n"
    assert plant.repoint_release_root(unit, tmp_path / "releases/new/src") == unit


def test_reconcile_services_repoints_a_site_declared_unit(tmp_path: Path, monkeypatch) -> None:
    # A site-declared mind is covered by the dashboard but not generated by the
    # plant, so a pin advance must repoint only its PYTHONPATH release component
    # and preserve its custom ExecStart.
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(
        json.dumps(["core-genome.service", "core-coordination.service"]) + "\n", encoding="utf-8")
    coordination = home / "core-coordination.service"
    coordination.write_text(
        "[Service]\n"
        f"Environment=PYTHONPATH={tmp_path}/releases/old/src\n"
        "Environment=PATH=/site/bin:/usr/bin\n"
        "ExecStart=/site/bin/coordination --follow\n", encoding="utf-8")
    monkeypatch.setattr("mishe_tauftauf.runtime_source.source_for",
                        lambda home, default: tmp_path / "releases" / "new")
    fragments = {plant.unit_name("core", slug): home / plant.unit_name("core", slug)
                 for slug in (*plant.ROLES, "permissions", "ci", *plant.OUT_OF_BAND)}
    calls = _fake_systemctl(monkeypatch, fragments)

    plant.reconcile_services(home, "core", True, "/usr/bin/python3")

    text = coordination.read_text(encoding="utf-8")
    assert f"Environment=PYTHONPATH={tmp_path}/releases/new/src\n" in text
    assert "releases/old" not in text
    assert "ExecStart=/site/bin/coordination --follow\n" in text
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "restart", "core-coordination.service"] not in calls


def test_reconcile_services_edits_a_self_installed_fragment_in_place(tmp_path: Path, monkeypatch) -> None:
    # `body-research` installs a regular fragment with no site unit file; the
    # plant must edit that fragment rather than refuse its non-symlink shape.
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["core-body.service"]) + "\n", encoding="utf-8")
    fragment = tmp_path / "config/core-body.service"
    fragment.parent.mkdir()
    fragment.write_text(
        "[Service]\n"
        f"Environment=PYTHONPATH={tmp_path}/releases/old/src\n"
        "Environment=PATH=/site/bin:/usr/bin\n", encoding="utf-8")
    monkeypatch.setattr("mishe_tauftauf.runtime_source.source_for",
                        lambda home, default: tmp_path / "releases" / "new")
    fragments = {plant.unit_name("core", slug): home / plant.unit_name("core", slug)
                 for slug in (*plant.ROLES, "permissions", "ci", *plant.OUT_OF_BAND)}
    fragments["core-body.service"] = fragment
    _fake_systemctl(monkeypatch, fragments)

    plant.reconcile_services(home, "core", True, "/usr/bin/python3")

    assert not (home / "core-body.service").exists()
    text = fragment.read_text(encoding="utf-8")
    assert f"Environment=PYTHONPATH={tmp_path}/releases/new/src\n" in text
    assert "releases/old" not in text
