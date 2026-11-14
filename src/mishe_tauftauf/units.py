"""Generated systemd units: the only triggers the runtime has.

``render`` is pure and deterministic. ``sync`` writes exactly what render says
and removes only stale files it generated (each carries MARKER), so units that
belong to anything else are never touched. Each installed unit is written to a
hidden sibling and renamed into place, so a concurrent reload or doctor read
never sees a half-written file. ``doctor`` compares expected and installed files
and never reports GREEN on missing or unreadable evidence.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import signal_store

MARKER = "# mishe-generated: observation runtime (edit nothing here; run `mishe sync`)"
WAKE_TIMEOUT = 1800
SLACK = 300
# Roles whose path triggers are coalesced: a burst of signal writes waits this many seconds, then starts
# the wake once. Keep the window such that window + wake runtime leaves at most StartLimitBurst-1 starts
# per StartLimitIntervalUSec (the host drop-in allows 3 per 5 min). Senses is the measured offender;
# health hit start-limit-hit on 2026-11-12 (08:33:39, 08:34:18, 08:36:51, 08:38:07 starts, each a
# producer rewrite of an unchanged signal file), so it is coalesced the same way.
# network hit start-limit-hit on 2026-11-12 (13:34:44, 13:35:34, 13:37:39, 13:38:45 starts; DMs 300309-300313
# each touched inbox/network and each wake runs about a minute), so it is coalesced too.
WAKE_DEBOUNCE_SECONDS = {"senses": 120, "health": 120, "network": 120}
# Every other role gets the same coalescing window (DM 300343, 2026-11-12: genome hit start-limit-hit at
# 2026-11-11 with the direct path trigger, and a RED signal is a path trigger for any subscriber). Ordinary
# wake runtime is about a minute, so 120 s keeps two starts per 5 min at most.
WAKE_DEBOUNCE_DEFAULT_SECONDS = 120
# Upper bound of one debounce run: its window plus the start spacing (wake.START_SPACING_SECONDS, 150 s).
WAKE_DEBOUNCE_TIMEOUT = 600


def wake_debounce_seconds(role: str) -> int:
    return WAKE_DEBOUNCE_SECONDS.get(role, WAKE_DEBOUNCE_DEFAULT_SECONDS)


# The wake unit's own memory throttle. The generated unit declares it, and a wake that runs in the
# unit restores it at runtime when a protected host drop-in shadows it with a lower value (see
# ``ensure_wake_memory_high``): a wake pinned at its throttle makes no progress and runs out its
# engine budget instead (2026-11-10: five MiniMax wakes timed out at 1800 s at the 1 GiB mark).
WAKE_MEMORY_HIGH = "1536M"
WAKE_MEMORY_HIGH_BYTES = 1536 * 1024 * 1024


@dataclass
class Config:
    home: Path
    repo: Path
    python: str
    pythonpath: str
    unit_dir: Path
    prefix: str = "mishe"
    lifeline_roles: tuple = ("genome", "health")
    plants_slice: str | None = "mishe-plants.slice"
    lifeline_slice: str | None = "mishe-lifeline.slice"
    doctor_every: int = 300


@dataclass
class Producer:
    name: str
    command: list
    every_seconds: int
    timeout: int = 120


@dataclass
class SyncReport:
    written: list = field(default_factory=list)
    removed: list = field(default_factory=list)


@dataclass
class DoctorReport:
    state: str
    problems: list = field(default_factory=list)


def _env(cfg: Config) -> str:
    return (f"Environment=PYTHONPATH={cfg.pythonpath}\n"
            f"Environment=MISHE_SEED_HOME={cfg.home}\n"
            f"Environment=MISHE_REPO={cfg.repo}\n"
            "Environment=PATH=/home/mesh-home/.local/bin:/usr/local/bin:/usr/bin:/bin\n")


def _cmd(cfg: Config, *words: str) -> str:
    return " ".join([cfg.python, "-m", "mishe_tauftauf", "--home", str(cfg.home), *words])


def _exec_word(word: str) -> str:
    """One argv word as systemd reads an ExecStart token: whitespace splits it, so a word with a space
    (``living room``) is double-quoted; ``$`` and ``%`` are specifiers and are escaped as ``$$`` and ``%%``."""
    text = word.replace("%", "%%").replace("$", "$$")
    if text and not re.search(r'[\s"\\]', text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _self_args(cfg: Config) -> list:
    """The settings a doctor run needs to rebuild exactly what sync generated."""
    args = ["--unit-dir", str(cfg.unit_dir), "--prefix", cfg.prefix, "--repo", str(cfg.repo),
            "--pythonpath", cfg.pythonpath]
    if cfg.plants_slice is None and cfg.lifeline_slice is None:
        args.append("--no-slices")
    return args


def _file(*sections: str) -> str:
    return MARKER + "\n" + "\n".join(sections) + "\n"


def _is_wake_failure_signal(name: str, prefix: str) -> bool:
    """Failure notices written by a failed wake unit (OnFailure), which must not re-trigger wakes."""
    return name.startswith(f"failed-{prefix}-wake-")


def render(cfg: Config, roles, *, producers=()) -> dict:
    from .delivery_requests import admission_path
    p = cfg.prefix
    failed = f"OnFailure={p}-failed@%n.service"
    files = {}
    files[f"{p}-wake@.service"] = _file(
        f"[Unit]\nDescription=Mishe wake %i\n{failed}"
        # A start while a hold or delivery admission is present only returns "held": it does no work
        # but each start still spends the host StartLimitBurst budget, so a short burst of them turns
        # the wake RED (2026-11-11: mishe-wake@genome.service start-limit-hit, 73rd occurrence). A
        # start skipped by an unmet condition is not counted toward the limit (measured with a
        # path-triggered probe), so refuse the start instead of running the no-op.
        f"\nConditionPathExists=!{cfg.home / 'hold'}"
        f"\nConditionPathExists=!{admission_path(cfg.home)}",
        "[Service]\nType=oneshot\nUMask=0077\n" + f"WorkingDirectory={cfg.repo}\n" + _env(cfg)
        + f"ExecStart={_cmd(cfg, 'wake', '%i', '--repo', str(cfg.repo), '--timeout', str(WAKE_TIMEOUT))}\n"
        # Initial serialization check and dispatch/reconcile each have a
        # bounded lock wait. The engine retains its full configured budget.
        f"TimeoutStartSec={3 * WAKE_TIMEOUT + SLACK}\nTimeoutStopSec=20\nKillMode=control-group\n"
        # MemoryHigh throttles a wake before MemoryMax kills it: an engine plus a 760 MB tape scan
        # otherwise sits at the 1G prefix default and stalls on memory pressure. A protected host
        # drop-in can shadow this value; the wake restores its own floor at runtime (wake_memory).
        f"MemoryAccounting=yes\nMemoryHigh={WAKE_MEMORY_HIGH}\nMemoryMax=2G\nTasksMax=256\nCPUWeight=50\nIOWeight=50\n")
    registry = signal_store.registry(cfg.home)
    for role in sorted(roles):
        slice_name = cfg.lifeline_slice if role in cfg.lifeline_roles else cfg.plants_slice
        if slice_name:
            files[f"{p}-wake@{role}.service.d/10-slice.conf"] = _file(f"[Service]\nSlice={slice_name}")
        watched = [f"PathChanged={cfg.home}/inbox/{role}"]
        # A failed wake writes its own failure signal through OnFailure. Triggering on those would let two roles
        # re-fire each other (health <-> genome) and hit StartLimitBurst; the wake still reads them in its dossier.
        watched += [f"PathChanged={cfg.home}/signals/{name}.json"
                    for name, entry in sorted(registry.items())
                    if role in entry.get("subscribers", []) and not _is_wake_failure_signal(name, p)]
        # A path change starts this oneshot, which sleeps out the burst and then starts the wake once.
        # A change while it sleeps is a no-op (the unit is already active), so one burst of signal writes
        # becomes one wake start. Uncoalesced, senses restarted every ~100 s and hit StartLimitBurst=3
        # per 5 min (2026-11-12: 29 starts in 6 h, start-limit-hit at 07:51:58, 07:52:46, 07:57:51).
        target = f"{p}-wake-debounce-{role}.service"
        files[target] = _file(
            # OnFailure routes a refused debounce to the failed-unit signal (review of e77d2c1a: the raise was
            # only visible in the journal). The failed signal name is excluded from path triggers above.
            f"[Unit]\nDescription=Coalesce wake triggers for {role}\n{failed}",
            # Same interpreter, PYTHONPATH (the release pin) and working directory as the wake unit, so the
            # debounce imports the pinned code rather than whatever the user's environment resolves.
            "[Service]\nType=oneshot\n" + f"WorkingDirectory={cfg.repo}\n" + _env(cfg)
            # The debounce waits out the window and the shared start spacing, checks and records under one
            # ledger lock, then starts the wake itself (wake.debounced_start). No ExecStopPost start: that ran
            # after a failed or timed-out debounce and skipped the spacing. A failed debounce starts nothing;
            # the owed inbox is picked up by the next resume_owed.
            + f"ExecStart={_cmd(cfg, 'wake-debounce', role, '--window', str(wake_debounce_seconds(role)))}\n"
            + f"TimeoutStartSec={WAKE_DEBOUNCE_TIMEOUT}\n")
        files[f"{p}-wake-{role}.path"] = _file(
            f"[Unit]\nDescription=Mishe wake triggers for {role}",
            "[Path]\n" + "\n".join(watched) + f"\nUnit={target}",
            "[Install]\nWantedBy=paths.target")
    files[f"{p}-failed@.service"] = _file(
        "[Unit]\nDescription=Mishe failure report for %i",
        "[Service]\nType=oneshot\n" + _env(cfg) + f"ExecStart={_cmd(cfg, 'failed', '%i')}\nTimeoutStartSec=60\n")
    files[f"{p}-arm.service"] = _file(
        "[Unit]\nDescription=Re-arm the next-due timer",
        "[Service]\nType=oneshot\n" + _env(cfg) + f"ExecStart={_cmd(cfg, 'arm')}\n",
        "[Install]\nWantedBy=default.target")
    files[f"{p}-doctor.service"] = _file(
        f"[Unit]\nDescription=Mishe doctor\n{failed}",
        # Unbuffered so a run killed by TimeoutStartSec keeps the lines it already printed in the journal.
        "[Service]\nType=oneshot\n" + _env(cfg) + "Environment=PYTHONUNBUFFERED=1\n"
        + f"ExecStart={_cmd(cfg, 'doctor', *_self_args(cfg))}\nTimeoutStartSec=120\n")
    files[f"{p}-doctor.timer"] = _file(
        "[Unit]\nDescription=Run the Mishe doctor",
        f"[Timer]\nOnBootSec=60\nOnUnitActiveSec={cfg.doctor_every}\nAccuracySec=10s\nUnit={p}-doctor.service",
        "[Install]\nWantedBy=timers.target")
    # The root-fs stall probe is a long-running monitor, not a timer: it must come back after a
    # reboot or crash by itself (a transient unit does neither). Restart=always, because a clean
    # exit from a monitor would otherwise end the stall record silently.
    files[f"{p}-dual-probe.service"] = _file(
        f"[Unit]\nDescription=Mishe root-fs stall probe\n{failed}",
        # No PYTHONPATH pin: dual_probe.py is stdlib-only and runs with -I, so a release pin
        # would only make this a pinned non-oneshot, which the moving-pin observer rejects.
        "[Service]\nType=simple\nUMask=0077\n" + f"Environment=MISHE_SEED_HOME={cfg.home}\n"
        + f"WorkingDirectory={cfg.home}/operator\n"
        + f"ExecStart=/usr/bin/python3 -I {cfg.home}/operator/dual_probe.py\n"
        + "Restart=always\nRestartSec=60\n"
        + f"Slice={cfg.plants_slice}\n" * bool(cfg.plants_slice),
        "[Install]\nWantedBy=default.target")
    for prod in producers:
        files[f"{p}-producer-{prod.name}.service"] = _file(
            f"[Unit]\nDescription=Mishe producer {prod.name}\n{failed}",
            "[Service]\nType=oneshot\nUMask=0077\n" + _env(cfg)
            # A producer under MemoryHigh=1G stalled the root-fs fsync on 2026-11-13 (16 s stalls while
            # reclaim throttled the slice); 1536M cleared the stalls at runtime. Persist it here. Only the
            # soft MemoryHigh throttle is set: a hard MemoryMax kill has no evidence behind it.
            + f"ExecStart={' '.join(_exec_word(w) for w in prod.command)}\nTimeoutStartSec={prod.timeout}\n"
            + "MemoryAccounting=yes\nMemoryHigh=1536M\n"
            + f"Slice={cfg.plants_slice}\n" * bool(cfg.plants_slice))
        # Producers with the same period started together fire in lockstep (about 17 at one second every
        # period), a write burst that stalled the root-fs probe's fsync on 2026-11-13. A per-activation
        # random delay breaks that lockstep; it is a quarter of the period, capped at 120 s, so short
        # timers are not pushed far.
        jitter = max(1, min(120, prod.every_seconds // 4))
        files[f"{p}-producer-{prod.name}.timer"] = _file(
            f"[Unit]\nDescription=Run producer {prod.name}",
            f"[Timer]\nOnBootSec=30\nOnUnitActiveSec={prod.every_seconds}\nRandomizedDelaySec={jitter}\nAccuracySec=5s\nUnit={p}-producer-{prod.name}.service",
            "[Install]\nWantedBy=timers.target")
    return files


def signal_name(unit: str) -> str:
    """The signal that records a failure of ``unit`` (unit names carry @ and .)."""
    return "failed-" + re.sub(r"[^a-z0-9_-]+", "-", unit.lower()).strip("-")


def producer_unit_for_signal(name: str) -> str | None:
    """The producer unit whose failure signal is ``name``, or None. ``failed-mishe-producer-X-service`` is
    ``mishe-producer-X.service``; accepted only when ``signal_name`` maps it back to ``name``, so a lossy mapping cannot
    name a different unit (failure.clear_recovered uses this for incidents that predate the ``unit`` field)."""
    if not (name.startswith("failed-mishe-producer-") and name.endswith("-service")):
        return None
    candidate = name[len("failed-"):-len("-service")] + ".service"
    return candidate if signal_name(candidate) == name else None


PLANT_UNIT_PREFIXES = ("mishe-wake", "mishe-producer-", "mishe-failed@", "mishe-doctor", "mishe-arm", "mishe-next-due")


def is_plant_unit(name: str) -> bool:
    """Whether a unit is one this runtime generated, not a foreign consumer with a ``mishe-`` prefix."""
    return name.startswith(PLANT_UNIT_PREFIXES)


def is_health_wake(unit: str, prefix: str = "mishe") -> bool:
    # The health debounce is part of health's wake: its failure must not be routed to health's own inbox,
    # which the debounce watches (review of 20f172c7: a self-routed failure retriggers the debounce).
    return unit in {f"{prefix}-wake@health.service", f"{prefix}-wake-debounce-health.service"}


def user_timer_names(glob: str) -> list[str]:
    """Names of user timers matching ``glob``, loaded or not (systemctl output decoded here)."""
    listed = subprocess.run(
        ["systemctl", "--user", "list-units", "--type=timer", "--all", "--plain",
         "--no-legend", glob],
        check=False, capture_output=True, text=True).stdout
    return [line.split()[0] for line in listed.splitlines() if line.strip()]


def timers_run_release(timers: list[str], root: str) -> bool:
    """True when every timer's service runs the release at ``root`` (PYTHONPATH decoded here)."""
    for timer in timers:
        service = subprocess.run(["systemctl", "--user", "show", timer, "-p", "Triggers", "--value"],
                                 check=False, capture_output=True, text=True).stdout.strip()
        if not service:
            continue  # no such unit under that name
        env = subprocess.run(["systemctl", "--user", "show", service, "-p", "Environment", "--value"],
                             check=False, capture_output=True, text=True).stdout.split()
        if f"PYTHONPATH={root}" not in env:
            return False
    return True


def manager_error(result) -> str:
    """A short manager error from a failed systemd-run/ctl call, decoded here."""
    text = (result.stderr or result.stdout or b"")
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return text.strip() or f"rc={result.returncode}"


def unit_file_service_names(listing: str, prefix: str) -> list[str]:
    """Installed non-template services under ``prefix`` from ``list-unit-files`` text.

    The first column of each row is the unit name. A template such as
    ``name@.service`` is left out: ``systemctl show`` rejects a whole batch when
    one of its names is a template.
    """
    units: list[str] = []
    for row in listing.splitlines():
        columns = row.split()
        if not columns:
            continue
        unit = columns[0]
        if unit.startswith(prefix) and unit.endswith(".service") and "@" not in unit:
            units.append(unit)
    return units


def _write_unit(path: Path, text: str) -> None:
    """Install one unit file atomically, so no reader or reload sees a half-written unit.

    A concurrent ``systemctl daemon-reload`` or ``doctor`` read once observed truncated
    units while ``sync`` rewrote them (2026-11-10: two arm producers failed arming the
    next-due timer in the same window as a reload storm). Write a hidden sibling — systemd
    ignores hidden files and ``_installed`` skips them, so a leak from an interrupted run
    is inert — then rename it into place.
    """
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _installed(cfg: Config) -> dict:
    found = {}
    if not cfg.unit_dir.is_dir():
        return found
    for path in sorted(cfg.unit_dir.rglob("*")):
        if path.name.startswith("."):                      # systemd ignores hidden files; so do we
            continue
        if path.is_file() and not path.is_symlink() and not any(part.endswith(".wants") for part in path.parts):
            rel = str(path.relative_to(cfg.unit_dir))
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            if text.startswith(MARKER):
                found[rel] = text
    return found


def sync(cfg: Config, roles, *, producers=(), runner=None, uninstall: bool = False,
         enable: bool = True) -> SyncReport:
    """Write generated units, remove stale generated ones, reload systemd.

    ``uninstall`` renders nothing, so every generated unit is stale and goes. A
    trigger is disabled before its file is removed, so no enable link dangles. With
    ``enable=False`` the files are installed and systemd reloaded, but no trigger is
    enabled or started: installed, not running.
    """
    if not uninstall:
        if "doctor" not in signal_store.registry(cfg.home):
            signal_store.register(cfg.home, "doctor", max_age=3 * cfg.doctor_every, subscribers=["health"])
        for name in ("inbox", "signals"):       # watched parents must exist; the watched files must not
            (cfg.home / name).mkdir(parents=True, exist_ok=True)
    expected = {} if uninstall else render(cfg, roles, producers=producers)
    report = SyncReport()
    for rel, text in expected.items():
        path = cfg.unit_dir / rel
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_unit(path, text)
            report.written.append(rel)
    stale = [rel for rel in _installed(cfg) if rel not in expected]
    if runner is not None:
        for rel in stale:
            if rel.endswith((".path", ".timer")):
                runner(["systemctl", "--user", "disable", "--now", rel])
    for rel in stale:
        (cfg.unit_dir / rel).unlink()
        report.removed.append(rel)
    if runner is not None and (report.written or report.removed):
        runner(["systemctl", "--user", "daemon-reload"])
    if runner is not None and enable:
        for rel in sorted(expected):                 # desired state, not the diff: idempotent
            if rel.endswith((".path", ".timer")):
                runner(["systemctl", "--user", "enable", "--now", rel])
            elif rel.endswith("-arm.service"):
                runner(["systemctl", "--user", "enable", rel])      # re-arms the next-due timer at boot
    marker = cfg.home / "units-installed"
    if uninstall or not enable:
        marker.unlink(missing_ok=True)
    elif runner is not None:
        marker.write_text("units installed and enabled by mishe sync\n", encoding="utf-8")
    return report


def _pin_import_problems(pythonpath: str) -> list:
    """Every relative import in the pinned package must resolve inside that pin.

    A unit can be GREEN while its PYTHONPATH pin is missing a module the package
    imports (the observer-checks failure of 2026-11-05 imported revive_boundary
    from a stale runtime). Static and deterministic: nothing is executed.
    """
    pkg = Path(pythonpath) / "mishe_tauftauf"
    if not (pkg / "__init__.py").is_file():
        return [f"pin {pythonpath}: mishe_tauftauf package missing"]

    def has_module(base_dir: Path, parts) -> bool:
        base = base_dir.joinpath(*parts)
        return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()

    problems = []
    for src in sorted(pkg.rglob("*.py")):
        rel = src.relative_to(pkg)
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            problems.append(f"pin {rel}: unreadable ({type(exc).__name__})")
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.level >= 1):
                continue
            # A relative import resolves from the file's own package, one directory up per extra dot.
            anchor = src.parent
            for _ in range(node.level - 1):
                anchor = anchor.parent
            if not anchor.is_relative_to(pkg):
                problems.append(f"pin {rel}: relative import escapes the package")
                continue
            if node.module:
                parts = node.module.split(".")
                if not has_module(anchor, parts):
                    problems.append(f"pin {rel}: imports missing module {node.module}")
                    continue
                target = anchor.joinpath(*parts)
                target_file = target.with_suffix(".py") if target.with_suffix(".py").is_file() else target / "__init__.py"
                known = _module_names(target_file)
                for alias in node.names:
                    # A name is reported missing only when the target is fully static (no star import, no __getattr__) and the name
                    # is neither defined/imported there nor a submodule: a stale pin can lack a symbol as well as a module.
                    if alias.name != "*" and known is not None and alias.name not in known and not has_module(anchor.joinpath(*parts), [alias.name]):
                        problems.append(f"pin {rel}: {node.module} does not define {alias.name}")
            else:
                exported = _module_names(anchor / "__init__.py")
                for alias in node.names:
                    if alias.name != "*" and not has_module(anchor, [alias.name]) and (exported is None or alias.name not in exported):
                        problems.append(f"pin {rel}: imports missing module {alias.name}")
    return problems


def _module_names(path: Path):
    """Top-level names a module defines or imports (including inside top-level if/try/with/for blocks), or None when that cannot be
    decided statically (unreadable, a star import or a module ``__getattr__``): unknown is never reported as missing."""
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None
    if any(marker in text for marker in ("globals()", "__dict__", "sys.modules", "setattr(")):
        return None                                   # the namespace is built at run time: undecidable, so never reported as missing
    names, dynamic = set(), [False]

    def targets(node):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                targets(item)
        elif isinstance(node, ast.Starred):
            targets(node.value)

    def visit(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Import):
                names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        dynamic[0] = True
                    names.add(alias.asname or alias.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    targets(target)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                targets(node.target); visit(node.body); visit(node.orelse)
            elif isinstance(node, (ast.If, ast.While)):
                visit(node.body); visit(node.orelse)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                visit(node.body)
            elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
                visit(node.body); visit(node.orelse); visit(node.finalbody)
                for handler in node.handlers:
                    visit(handler.body)
            elif isinstance(node, ast.Match):
                for case in node.cases:
                    visit(case.body)
            elif type(node).__name__ == "TypeAlias":
                targets(node.name)
            elif hasattr(node, "body") and isinstance(getattr(node, "body"), list):
                dynamic[0] = True                      # a statement kind this scan does not know that nests statements: undecidable

    visit(tree.body)
    if dynamic[0] or "__getattr__" in names:
        return None
    return names


def _wrapper_pythonpath(wrapper: Path):
    """The PYTHONPATH the site's operator wrapper exports, or None when it names none."""
    for line in wrapper.read_text(encoding="utf-8").splitlines():
        if line.startswith("export PYTHONPATH="):
            return line[len("export PYTHONPATH="):].strip().strip("'\"")
    return None


def _wrapper_pin_problems(home: Path) -> list:
    """The operator wrapper's PYTHONPATH pin must import every module it names.

    The 2026-11-05 renderer failure came through this wrapper: its stale runtime pin
    lacked revive_boundary while the units stayed GREEN. Unreadable or unpinned
    wrappers are reported, never treated as clean.
    """
    wrapper = home / "bin" / "mishe-tauftauf"
    try:
        pin = _wrapper_pythonpath(wrapper)
    except OSError as exc:
        return [f"wrapper {wrapper.name}: unreadable ({type(exc).__name__})"]
    if pin is None:
        return [f"wrapper {wrapper.name}: exports no PYTHONPATH pin"]
    return [f"wrapper {p}" for p in _pin_import_problems(pin)]


def doctor(cfg: Config, roles, *, producers=()) -> DoctorReport:
    try:
        expected = render(cfg, roles, producers=producers)
        installed = _installed(cfg)
    except OSError as exc:
        return DoctorReport("UNKNOWN", [f"cannot read installed units: {exc}"])
    problems = _pin_import_problems(cfg.pythonpath) + _wrapper_pin_problems(cfg.home)
    for rel, text in expected.items():
        if rel not in installed:
            problems.append(f"{rel}: missing")
        elif installed[rel] != text:
            problems.append(f"{rel}: drift from generated content (run `mishe sync` to regenerate)")
    for rel in installed:
        if rel not in expected:
            problems.append(f"{rel}: stale generated unit")
    return DoctorReport("RED" if problems else "GREEN", problems)
