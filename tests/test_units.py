from pathlib import Path

import pytest

from mishe_tauftauf import signal_store as store, units

ROLES = ["genome", "health", "docs"]


@pytest.fixture
def cfg(tmp_path):
    home = tmp_path / "site"
    store.register(home, "ci", max_age=600, subscribers=["genome"])
    pin = tmp_path / "rel" / "src"                      # a real (empty) pinned package, so doctor sees a valid pin
    (pin / "mishe_tauftauf").mkdir(parents=True)
    (pin / "mishe_tauftauf" / "__init__.py").write_text("", encoding="utf-8")
    (home / "bin").mkdir(parents=True)                  # a complete operator wrapper, so doctor sees a valid pin
    (home / "bin" / "mishe-tauftauf").write_text(
        f"#!/bin/sh\nexport PYTHONPATH={pin}\nexec python -m mishe_tauftauf \"$@\"\n", encoding="utf-8")
    return units.Config(home=home, repo=tmp_path / "repo", python="/usr/bin/python3",
                        pythonpath=str(pin), unit_dir=tmp_path / "units")


def test_render_is_deterministic_and_marked(cfg):
    a = units.render(cfg, ROLES)
    assert a == units.render(cfg, ROLES)
    assert all(text.startswith(units.MARKER) for text in a.values())


def test_every_role_gets_a_path_unit_watching_inbox_and_subscribed_signals(cfg):
    files = units.render(cfg, ROLES)
    genome = files["mishe-wake-genome.path"]
    assert f"PathChanged={cfg.home}/inbox/genome" in genome
    assert f"PathChanged={cfg.home}/signals/ci.json" in genome
    assert "Unit=mishe-wake@genome.service" in genome
    assert "signals/ci" not in files["mishe-wake-docs.path"]
    assert ".seen" not in "".join(files.values())            # freshness sidecar never triggers


def test_wake_service_is_bounded_oneshot_with_onfailure(cfg):
    svc = units.render(cfg, ROLES)["mishe-wake@.service"]
    for needle in ("Type=oneshot", "TimeoutStartSec=", "MemoryMax=", "OnFailure=mishe-failed@%n.service",
                   "wake %i", f"MISHE_SEED_HOME={cfg.home}", "KillMode=control-group"):
        assert needle in svc
    # RuntimeMaxSec is ignored for Type=oneshot; the start timeout is the real hard cap.
    assert "RuntimeMaxSec=" not in svc


def test_lifeline_roles_get_the_lifeline_slice_dropin(cfg):
    files = units.render(cfg, ROLES)
    assert "Slice=mishe-lifeline.slice" in files["mishe-wake@genome.service.d/10-slice.conf"]
    assert "Slice=mishe-plants.slice" in files["mishe-wake@docs.service.d/10-slice.conf"]


def test_no_alert_units_and_one_failure_target(cfg):
    names = units.render(cfg, ROLES)
    assert "mishe-failed@.service" in names
    assert not [n for n in names if "alert" in n]


def test_producer_gets_timer_and_oneshot_service(cfg):
    producers = [units.Producer(name="devto", command=["/bin/true"], every_seconds=300, timeout=60)]
    files = units.render(cfg, ROLES, producers=producers)
    assert "OnUnitActiveSec=300" in files["mishe-producer-devto.timer"] or "OnCalendar" in files["mishe-producer-devto.timer"]
    assert "OnFailure=mishe-failed@%n.service" in files["mishe-producer-devto.service"]


def test_sync_writes_and_is_idempotent_and_removes_stale_generated_only(cfg):
    calls = []
    cfg.unit_dir.mkdir(parents=True)
    (cfg.unit_dir / "android-body-genome.service").write_text("[Unit]\n")        # not ours
    (cfg.unit_dir / "mishe-wake-gone.path").write_text(units.MARKER + "\n[Path]\n")  # ours, stale
    first = units.sync(cfg, ROLES, runner=calls.append)
    assert "mishe-wake-gone.path" in first.removed and not (cfg.unit_dir / "mishe-wake-gone.path").exists()
    assert (cfg.unit_dir / "android-body-genome.service").exists()
    second = units.sync(cfg, ROLES, runner=calls.append)
    assert second.written == [] and second.removed == []


def test_doctor_green_when_units_match(cfg):
    units.sync(cfg, ROLES, runner=lambda c: None)
    report = units.doctor(cfg, ROLES)
    assert report.state == "GREEN" and report.problems == []


def test_doctor_red_on_drift_and_missing_never_silent(cfg):
    units.sync(cfg, ROLES, runner=lambda c: None)
    (cfg.unit_dir / "mishe-wake-genome.path").write_text(units.MARKER + "\ntampered\n")
    (cfg.unit_dir / "mishe-wake@.service").unlink()
    report = units.doctor(cfg, ROLES)
    assert report.state == "RED"
    assert any("mishe-wake-genome.path" in p and "drift" in p for p in report.problems)
    assert any("mishe-wake@.service" in p and "missing" in p for p in report.problems)


def test_doctor_unknown_when_unit_dir_unreadable(cfg):
    assert units.doctor(cfg, ROLES).state == "RED"            # nothing installed yet = not GREEN
    assert units.doctor(units.Config(**{**cfg.__dict__, "unit_dir": cfg.unit_dir / "x" / "y"}), ROLES).state != "GREEN"


def test_doctor_unit_carries_the_config_it_was_generated_with(cfg):
    cfg.prefix, cfg.plants_slice, cfg.lifeline_slice = "scratch", None, None
    svc = units.render(cfg, ROLES)["scratch-doctor.service"]
    for needle in (f"--unit-dir {cfg.unit_dir}", "--prefix scratch", f"--repo {cfg.repo}",
                   f"--pythonpath {cfg.pythonpath}", "--no-slices"):
        assert needle in svc


def test_path_units_never_create_the_watched_files_as_directories(cfg):
    assert "MakeDirectory" not in "".join(units.render(cfg, ROLES).values())


def test_sync_creates_the_watched_parent_directories_not_the_files(cfg):
    units.sync(cfg, ROLES, runner=lambda c: None)
    assert (cfg.home / "inbox").is_dir() and (cfg.home / "signals").is_dir()
    assert not (cfg.home / "inbox" / "genome").exists()       # a pre-created inbox would read as mail


def test_enable_symlinks_are_not_stale_units(cfg):
    units.sync(cfg, ROLES, runner=lambda c: None)
    wants = cfg.unit_dir / "paths.target.wants"
    wants.mkdir()
    (wants / "mishe-wake-genome.path").symlink_to(cfg.unit_dir / "mishe-wake-genome.path")
    assert units.doctor(cfg, ROLES).state == "GREEN"
    assert units.sync(cfg, ROLES, runner=lambda c: None).removed == []
    assert (wants / "mishe-wake-genome.path").is_symlink()


def test_uninstall_disables_then_removes_only_generated_units(cfg):
    calls = []
    units.sync(cfg, ROLES, runner=lambda c: None)
    (cfg.unit_dir / "android-body-genome.service").write_text("[Unit]\n")
    report = units.sync(cfg, [], uninstall=True, runner=calls.append)
    assert (cfg.unit_dir / "android-body-genome.service").exists()
    assert not [p for p in cfg.unit_dir.rglob("*") if p.is_file() and p.name.startswith("mishe-")]
    disabled = [c for c in calls if "disable" in c]
    assert disabled and any("mishe-wake-genome.path" in " ".join(c) for c in disabled)
    assert any("daemon-reload" in c for c in calls)
    assert "mishe-wake-genome.path" in report.removed


def test_removed_triggers_are_disabled_before_their_files_go(cfg):
    calls = []
    units.sync(cfg, ROLES, runner=lambda c: None)
    units.sync(cfg, ["genome", "health"], runner=calls.append)         # docs dropped
    order = [(" ".join(c)) for c in calls]
    assert any("disable" in c and "mishe-wake-docs.path" in c for c in order)


def test_sync_without_enable_installs_files_but_starts_no_trigger(cfg):
    calls = []
    units.sync(cfg, ROLES, runner=calls.append, enable=False)
    assert (cfg.unit_dir / "mishe-wake-genome.path").exists()
    assert any("daemon-reload" in c for c in calls)
    assert not [c for c in calls if "enable" in c or "start" in c]


def test_sync_enables_the_boot_arm_service_and_marks_the_site_as_installed(cfg):
    calls = []
    units.sync(cfg, ROLES, runner=calls.append)
    assert any(c[-1] == "mishe-arm.service" and "enable" in c for c in calls)
    assert (cfg.home / "units-installed").exists()          # lets `mishe arm` schedule the next-due timer


def test_held_install_and_uninstall_leave_no_installed_marker(cfg):
    units.sync(cfg, ROLES, runner=lambda c: None, enable=False)
    assert not (cfg.home / "units-installed").exists()
    units.sync(cfg, ROLES, runner=lambda c: None)
    units.sync(cfg, [], uninstall=True, runner=lambda c: None)
    assert not (cfg.home / "units-installed").exists()


def test_install_held_then_sync_enables_every_trigger_even_though_files_are_unchanged(cfg):
    units.sync(cfg, ROLES, runner=lambda c: None, enable=False)       # the held install
    calls = []
    report = units.sync(cfg, ROLES, runner=calls.append)              # the start: nothing to write
    assert report.written == []
    enabled = {c[-1] for c in calls if "enable" in c}
    assert "mishe-wake-genome.path" in enabled and "mishe-doctor.timer" in enabled and "mishe-arm.service" in enabled
    assert (cfg.home / "units-installed").exists()


def test_outer_wake_lifetime_covers_both_queue_acquisitions_and_engine(cfg):
    import re
    from mishe_tauftauf import runtime_cli
    svc=units.render(cfg,ROLES)["mishe-wake@.service"]
    lifetime=int(re.search(r"TimeoutStartSec=(\d+)",svc).group(1))
    worst_queue=2*runtime_cli.WAKE_TIMEOUT
    assert lifetime>=worst_queue+runtime_cli.WAKE_TIMEOUT+units.SLACK


def _pin(tmp_path, files):
    pkg = tmp_path / "pin" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    for name, text in files.items():
        (pkg / name).write_text(text, encoding="utf-8")
    return str(tmp_path / "pin")


def test_pin_import_check_green_when_every_relative_import_resolves(tmp_path):
    pin = _pin(tmp_path, {"__init__.py": "", "a.py": "from . import b\nfrom .c import x\n",
                          "b.py": "", "c.py": "x = 1\n"})
    assert units._pin_import_problems(pin) == []


def test_pin_import_check_red_on_missing_module_even_when_lazy(tmp_path):
    pin = _pin(tmp_path, {"__init__.py": "", "a.py": "def f():\n    from .gone import g\n"})
    problems = units._pin_import_problems(pin)
    assert problems == ["pin a.py: imports missing module gone"]


def test_pin_import_check_red_when_package_absent(tmp_path):
    assert units._pin_import_problems(str(tmp_path / "nowhere")) != []


def test_doctor_red_when_pin_is_missing_a_module_the_units_run(cfg, tmp_path):
    pin = _pin(tmp_path, {"__init__.py": "", "a.py": "from .gone import g\n"})
    cfg = units.Config(**{**cfg.__dict__, "pythonpath": pin})
    units.sync(cfg, ROLES, runner=lambda c: None)
    report = units.doctor(cfg, ROLES)
    assert report.state == "RED" and any("gone" in p for p in report.problems)


def _wrapper(home, pin):
    (home / "bin").mkdir(parents=True, exist_ok=True)
    (home / "bin" / "mishe-tauftauf").write_text(
        f"#!/bin/sh\nexport PYTHONPATH={pin}\nexec python -m mishe_tauftauf \"$@\"\n", encoding="utf-8")


def test_wrapper_pin_red_when_wrapper_pin_misses_a_module(cfg, tmp_path):
    pin = _pin(tmp_path, {"__init__.py": "", "a.py": "from .gone import g\n"})
    _wrapper(cfg.home, pin)
    assert units._wrapper_pin_problems(cfg.home) == ["wrapper pin a.py: imports missing module gone"]


def test_wrapper_pin_green_when_wrapper_pin_is_complete(cfg, tmp_path):
    pin = _pin(tmp_path, {"__init__.py": "", "a.py": "from . import b\n", "b.py": ""})
    _wrapper(cfg.home, pin)
    assert units._wrapper_pin_problems(cfg.home) == []


def test_wrapper_pin_red_when_wrapper_missing_or_unpinned(cfg):
    (cfg.home / "bin" / "mishe-tauftauf").unlink()
    assert units._wrapper_pin_problems(cfg.home) != []
    (cfg.home / "bin" / "mishe-tauftauf").write_text("#!/bin/sh\nexec python\n", encoding="utf-8")
    assert units._wrapper_pin_problems(cfg.home) == ["wrapper mishe-tauftauf: exports no PYTHONPATH pin"]


def test_doctor_red_when_operator_wrapper_pin_is_stale_but_units_match(cfg, tmp_path):
    _wrapper(cfg.home, _pin(tmp_path, {"__init__.py": "", "a.py": "from .gone import g\n"}))
    units.sync(cfg, ROLES, runner=lambda c: None)
    report = units.doctor(cfg, ROLES)
    assert report.state == "RED" and any("gone" in p for p in report.problems)
