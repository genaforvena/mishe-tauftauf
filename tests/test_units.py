import sys
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
    assert "Unit=mishe-wake-debounce-genome.service" in genome
    assert "signals/ci" not in files["mishe-wake-docs.path"]
    assert ".seen" not in "".join(files.values())            # freshness sidecar never triggers


def test_wake_service_is_bounded_oneshot_with_onfailure(cfg):
    svc = units.render(cfg, ROLES)["mishe-wake@.service"]
    for needle in ("Type=oneshot", "TimeoutStartSec=", "MemoryHigh=1536M", "MemoryMax=", "OnFailure=mishe-failed@%n.service",
                   "wake %i", f"MISHE_SEED_HOME={cfg.home}", "KillMode=control-group"):
        assert needle in svc
    # RuntimeMaxSec is ignored for Type=oneshot; the start timeout is the real hard cap.
    assert "RuntimeMaxSec=" not in svc


def test_wake_start_is_refused_while_a_hold_or_delivery_admission_stands(cfg):
    # 2026-11-11: a held plant's path-triggered wake starts spent the host StartLimitBurst budget and
    # turned mishe-wake@genome.service RED (73rd start-limit-hit). A start skipped by an unmet condition
    # is not counted toward the limit, so the generated wake now refuses the no-op start entirely.
    svc = units.render(cfg, ROLES)["mishe-wake@.service"]
    assert f"ConditionPathExists=!{cfg.home}/hold" in svc
    assert f"ConditionPathExists=!{cfg.home}/work/delivery-admission.json" in svc


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
    assert "MemoryHigh=1536M" in files["mishe-producer-devto.service"]


def test_producer_timers_are_jittered_so_equal_periods_do_not_fire_in_lockstep(cfg):
    producers = [
        units.Producer(name="fast", command=["/bin/true"], every_seconds=300, timeout=60),
        units.Producer(name="tiny", command=["/bin/true"], every_seconds=8, timeout=60),
        units.Producer(name="slow", command=["/bin/true"], every_seconds=3600, timeout=60),
    ]
    files = units.render(cfg, ROLES, producers=producers)
    assert "RandomizedDelaySec=75" in files["mishe-producer-fast.timer"]
    assert "RandomizedDelaySec=2" in files["mishe-producer-tiny.timer"]
    assert "RandomizedDelaySec=120" in files["mishe-producer-slow.timer"]
    assert "AccuracySec=5s" in files["mishe-producer-fast.timer"]


def test_producer_execstart_keeps_a_spaced_argument_as_one_word(cfg):
    # systemd splits ExecStart on whitespace: an unquoted "living room" reached the collector as two arguments,
    # argparse rejected "room" (exit 2) and the household camera signal went RED on 2026-11-07.
    cmd = ["/usr/bin/python3", "tool.py", "camera", "--room", "living room", "--path", "a$b%c\\d\"e"]
    producers = [units.Producer(name="cam", command=cmd, every_seconds=60, timeout=120)]
    line = next(l for l in units.render(cfg, ROLES, producers=producers)["mishe-producer-cam.service"].splitlines()
                if l.startswith("ExecStart="))
    assert '"living room"' in line
    assert "a$$b%%c" in line
    assert "ExecStart=/usr/bin/python3 tool.py camera --room " in line


def test_dual_probe_is_a_durable_restarting_monitor_routed_to_failure(cfg):
    svc = units.render(cfg, ROLES)["mishe-dual-probe.service"]
    for needle in ("Type=simple", "Restart=always", "RestartSec=60", "WantedBy=default.target",
                   f"ExecStart=/usr/bin/python3 -I {cfg.home}/operator/dual_probe.py",
                   "OnFailure=mishe-failed@%n.service", f"WorkingDirectory={cfg.home}/operator",
                   "Slice=mishe-plants.slice", f"MISHE_SEED_HOME={cfg.home}"):
        assert needle in svc
    assert "Type=oneshot" not in svc and "RuntimeMaxSec" not in svc
    assert "PYTHONPATH" not in svc      # stdlib-only probe: a release pin would make it a pinned non-oneshot


def test_dual_probe_has_no_slice_when_scratch_namespaces_are_used(cfg):
    scratch = units.Config(home=cfg.home, repo=cfg.repo, python=cfg.python, pythonpath=cfg.pythonpath,
                           unit_dir=cfg.unit_dir, plants_slice=None, lifeline_slice=None)
    assert "Slice=" not in units.render(scratch, ROLES)["mishe-dual-probe.service"]


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


def test_a_failed_unit_write_leaves_the_installed_unit_intact(cfg, monkeypatch):
    calls = []
    units.sync(cfg, ROLES, runner=calls.append)
    path = cfg.unit_dir / "mishe-wake-genome.path"
    before = path.read_text(encoding="utf-8")
    changed = dict(units.render(cfg, ROLES))                # a real render, so sync wants to rewrite
    changed["mishe-wake-genome.path"] += "# changed\n"
    monkeypatch.setattr(units, "render", lambda *a, **k: changed)

    def refuse_replace(*_a, **_k):
        raise OSError("read-only unit dir")

    monkeypatch.setattr(units.os, "replace", refuse_replace)
    with pytest.raises(OSError):
        units.sync(cfg, ROLES, runner=calls.append)
    assert path.read_text(encoding="utf-8") == before       # the old unit survives, whole
    assert not [p for p in cfg.unit_dir.iterdir() if p.name.startswith(".")]


def test_a_leaked_hidden_temp_file_is_not_read_as_an_installed_unit(cfg):
    units.sync(cfg, ROLES, runner=lambda c: None)
    assert units.doctor(cfg, ROLES).state == "GREEN"
    (cfg.unit_dir / ".mishe-wake-genome.path.999.tmp").write_text(units.MARKER + "\n[Path]\n")
    assert units.doctor(cfg, ROLES).problems == []          # a crash's leftover must not RED the site


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
        (pkg / name).parent.mkdir(parents=True, exist_ok=True)
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


def test_pin_import_check_red_on_missing_module_inside_a_subpackage(tmp_path):
    # Review of 29e21834+: the scan was top-level only, so a stale subpackage (core/) passed the doctor check.
    pin = _pin(tmp_path, {"__init__.py": "", "core/__init__.py": "", "core/x.py": "from ..gone import g\n"})
    assert units._pin_import_problems(pin) == ["pin core/x.py: imports missing module gone"]


def test_pin_import_check_resolves_parent_relative_imports_from_the_subpackage(tmp_path):
    pin = _pin(tmp_path, {"__init__.py": "", "b.py": "y = 1\n", "core/__init__.py": "", "core/x.py": "from ..b import y\nfrom .. import b\n"})
    assert units._pin_import_problems(pin) == []


def test_pin_import_check_red_when_a_relative_import_escapes_the_package(tmp_path):
    pin = _pin(tmp_path, {"__init__.py": "", "a.py": "from ... import outside\n"})
    assert units._pin_import_problems(pin) == ["pin a.py: relative import escapes the package"]


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


def test_pin_import_check_accepts_a_name_the_package_init_exports_but_still_flags_a_missing_one(tmp_path):
    # Independent review of moving-pin: `from . import NAME` may name something __init__.py defines or imports, not only a module.
    pin = _pin(tmp_path, {"__init__.py": "VERSION = '1'\nfrom .b import helper\ndef util():\n    return 1\n",
                          "b.py": "def helper():\n    return 2\n",
                          "a.py": "from . import VERSION, util, helper\n"})
    assert units._pin_import_problems(pin) == []
    pin2 = _pin(tmp_path / "other", {"__init__.py": "VERSION = '1'\n", "a.py": "from . import VERSION, nothing_here\n"})
    assert units._pin_import_problems(pin2) == ["pin a.py: imports missing module nothing_here"]


def test_pin_import_check_flags_a_symbol_the_existing_module_lacks_but_not_dynamic_or_conditional_ones(tmp_path):
    # Independent review of the moving-pin delivery: a stale pin can lack a SYMBOL of an existing module, not only the module.
    pin = _pin(tmp_path, {"__init__.py": "", "a.py": "from .foo import present, absent\nfrom .cond import maybe\nfrom .dyn import anything\nfrom .star import thing\nfrom .pkg import sub\n",
                          "foo.py": "def present():\n    pass\n",
                          "cond.py": "try:\n    import x as maybe\nexcept ImportError:\n    maybe = None\n",
                          "dyn.py": "def __getattr__(name):\n    return 1\n",
                          "star.py": "from os.path import *\n",
                          "pkg/__init__.py": "", "pkg/sub.py": ""})
    assert units._pin_import_problems(pin) == ["pin a.py: foo does not define absent"]


def test_pin_import_check_never_reports_a_name_it_cannot_decide(tmp_path):
    # Review of the moving-pin delivery: a name bound inside a top-level match or a type alias must not make a usable pin RED,
    # a module that builds its namespace at run time is undecidable, and a name that is really absent is still reported.
    # The `type` statement is 3.12 syntax, so on older interpreters the same name is bound with a plain assignment: the module must
    # parse on every interpreter CI runs, otherwise the pin is reported UNKNOWN (unreadable) and the missing-name check never runs.
    type_alias = "type Alias = int\n" if sys.version_info >= (3, 12) else "Alias = int\n"
    pin = _pin(tmp_path, {"__init__.py": "", "a.py": "from .m import viaMatch, Alias\nfrom .m import missing_for_real\n",
                          "m.py": "import sys\nmatch sys.platform:\n    case 'linux':\n        viaMatch = 1\n    case _:\n        viaMatch = 2\n" + type_alias})
    assert units._pin_import_problems(pin) == ["pin a.py: m does not define missing_for_real"]
    # A module an interpreter genuinely cannot parse is UNKNOWN for that module and never makes a name look missing.
    broken = _pin(tmp_path / "broken", {"__init__.py": "", "a.py": "from .m import anything\n", "m.py": "def (:\n"})
    assert units._pin_import_problems(broken) == ["pin m.py: unreadable (SyntaxError)"]
    dyn = _pin(tmp_path / "dyn", {"__init__.py": "", "a.py": "from .m import anything\n", "m.py": "globals()['anything'] = 1\n"})
    assert units._pin_import_problems(dyn) == []


def test_unit_file_service_names_keeps_site_services_and_drops_templates():
    listing = (
        "mishe-wake@.service       static  -\n"
        "mishe-producer-host-capacity.service enabled -\n"
        "mishe-doctor.service      static  -\n"
        "mishe-doctor.timer        enabled -\n"
        "\n"
        "other.service             enabled -\n"
    )
    assert units.unit_file_service_names(listing, "mishe-") == [
        "mishe-producer-host-capacity.service", "mishe-doctor.service"]
    assert units.unit_file_service_names("", "mishe-") == []


def test_wake_failure_signals_do_not_retrigger_wakes(cfg):
    # 2026-11-10: health and genome re-fired each other through failed-mishe-wake-*-service signals and hit StartLimitBurst.
    store.register(cfg.home, "failed-mishe-wake-genome-service", max_age=604800, subscribers=["health"])
    store.register(cfg.home, "failed-mishe-wake-health-service", max_age=604800, subscribers=["genome"])
    store.register(cfg.home, "failed-mishe-producer-ci-service", max_age=604800, subscribers=["genome"])
    files = units.render(cfg, ROLES)
    health = files["mishe-wake-health.path"]
    genome = files["mishe-wake-genome.path"]
    assert "failed-mishe-wake-genome-service" not in health
    assert "failed-mishe-wake-health-service" not in genome
    assert "failed-mishe-producer-ci-service" in genome          # producer failures still trigger their subscribers
    assert f"PathChanged={cfg.home}/signals/ci.json" in genome   # ordinary subscriptions unaffected


def test_wake_failure_filter_follows_configured_prefix(cfg):
    # The filter must match the generated failure names for any prefix, not only the default one.
    import dataclasses
    scratch = dataclasses.replace(cfg, prefix="scratch")
    store.register(cfg.home, "failed-scratch-wake-genome-service", max_age=604800, subscribers=["health"])
    store.register(cfg.home, "failed-mishe-wake-genome-service", max_age=604800, subscribers=["health"])
    files = units.render(scratch, ROLES)
    health = files["scratch-wake-health.path"]
    assert "failed-scratch-wake-genome-service" not in health
    assert "failed-mishe-wake-genome-service" in health  # another prefix's signal is an ordinary subscription


def test_senses_path_triggers_go_through_a_debounce_that_starts_the_wake_once(cfg):
    files = units.render(cfg, ["senses", "genome"])
    debounce = files["mishe-wake-debounce-senses.service"]
    assert "wake-debounce senses --window 120" in debounce
    assert "ExecStopPost" not in debounce                  # the debounce starts the wake itself, after spacing
    assert "PYTHONPATH=" in debounce and "WorkingDirectory=" in debounce  # pinned, like the wake unit
    assert "Unit=mishe-wake-debounce-senses.service" in files["mishe-wake-senses.path"]
    assert "Unit=mishe-wake@senses.service" not in files["mishe-wake-senses.path"]
    # Every role is coalesced, genome included: its path trigger goes through its own debounce unit.
    assert "Unit=mishe-wake-debounce-genome.service" in files["mishe-wake-genome.path"]
    assert "Unit=mishe-wake@genome.service" not in files["mishe-wake-genome.path"]


def test_network_path_triggers_are_coalesced_like_health(cfg):
    # DM bursts to network (2026-11-12 13:34-13:38Z) started one wake per touch and hit StartLimitBurst=3.
    files = units.render(cfg, ["network", "genome"])
    debounce = files["mishe-wake-debounce-network.service"]
    assert "wake-debounce network --window 120" in debounce
    assert "ExecStopPost" not in debounce
    assert "Unit=mishe-wake-debounce-network.service" in files["mishe-wake-network.path"]
    assert "Unit=mishe-wake@network.service" not in files["mishe-wake-network.path"]
    assert "Unit=mishe-wake-debounce-genome.service" in files["mishe-wake-genome.path"]


def test_health_path_triggers_are_coalesced_like_senses(cfg):
    files = units.render(cfg, ["health", "genome"])
    debounce = files["mishe-wake-debounce-health.service"]
    assert "wake-debounce health --window 120" in debounce
    assert "ExecStopPost" not in debounce
    assert "Unit=mishe-wake-debounce-health.service" in files["mishe-wake-health.path"]
    assert "Unit=mishe-wake-debounce-genome.service" in files["mishe-wake-genome.path"]


def test_every_role_gets_a_coalescing_window_with_the_default_length(cfg):
    # DM 300343 (c): a burst of path triggers for any role must start its wake once, not once per touch.
    from mishe_tauftauf.units import WAKE_DEBOUNCE_DEFAULT_SECONDS, wake_debounce_seconds
    files = units.render(cfg, ["genome", "observer", "health"])
    assert wake_debounce_seconds("observer") == WAKE_DEBOUNCE_DEFAULT_SECONDS == 120
    assert "wake-debounce observer --window 120" in files["mishe-wake-debounce-observer.service"]
    for role in ("genome", "observer"):
        assert f"Unit=mishe-wake-debounce-{role}.service" in files[f"mishe-wake-{role}.path"]
        assert f"Unit=mishe-wake@{role}.service" not in files[f"mishe-wake-{role}.path"]


def test_producer_unit_for_signal_round_trips_or_is_none():
    # the derived unit is accepted only when signal_name maps it back to the signal, so a lossy name names no unit
    assert units.producer_unit_for_signal("failed-mishe-producer-work-trial-service") == "mishe-producer-work-trial.service"
    assert units.signal_name("mishe-producer-work-trial.service") == "failed-mishe-producer-work-trial-service"
    assert units.producer_unit_for_signal("failed-mishe-producer-work.trial-service") is None
    assert units.producer_unit_for_signal("failed-mishe-wake-docs-service") is None
    assert units.producer_unit_for_signal("failed-mishe-producer-X-service") is None


def test_sync_reloads_systemd_once_not_once_per_enabled_trigger(cfg):
    units.sync(cfg, ROLES, runner=lambda c: None, enable=False)       # the held install
    calls = []
    units.sync(cfg, ROLES, runner=calls.append)                       # nothing to write, all enabled
    reloads = [c for c in calls if "daemon-reload" in c]
    enables = [c for c in calls if "enable" in c]
    assert len(reloads) == 1
    assert enables and all("--no-reload" in c for c in enables)
