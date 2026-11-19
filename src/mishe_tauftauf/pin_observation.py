"""Observation of the live consumers after a pin move: the functional probe ``mishe release promote`` runs.

Run with the release tree as the working directory and its ``src`` on PYTHONPATH, with the site named
by MISHE_SEED_HOME (``promote`` passes both)::

    python -m mishe_tauftauf.pin_observation

The pin is one stable path (``SITE/runtime/current``), so every check names that path, never a sha.
The probe fails closed: any missing or contradictory observation is an error, never a pass.

  1. the stable pointer resolves to the release tree this probe runs from;
  2. every owned installed service is loaded from its unit file, needs no daemon reload and, when it is
     pinned, is a oneshot whose loaded PYTHONPATH equals the one on disk and the stable path;
  3. both operator wrappers name the stable path and no sha;
  4. the root-fs dual probe is installed and fresh;
  5. no consumer invocation that started before the pin moved is still running (a bounded drain of
     in-memory consumers, using the cutoff ``promote`` passes in MISHE_PIN_CUTOFF_US);
  6. the next-due timer is armed once more from the new pin after that drain, because a due handler
     that started before the move re-arms it with its own (old) release when it exits — the last
     writer wins, and the arm before the drain can be overwritten;
  7. every active or queued pinned consumer names the new pin.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from . import consumer_fence as fence
from . import release_promotion as rp
from . import release_boundary as boundary
from . import wall_patch

DRAIN_TIMEOUT_S = fence.DRAIN_TIMEOUT_S
# The observer enforces this budget itself with one monotonic deadline; wall_patch's kill is a
# hang net strictly above it. Reusing wall_patch's constant keeps the two from drifting apart.
OBSERVE_BUDGET_S = wall_patch.OBSERVE_BUDGET_S


class ObserverDeadlineExceeded(ValueError):
    """The end-to-end budget ran out before the pin could be confirmed: UNKNOWN, not a pass."""


def _check_deadline(deadline, stage):
    if deadline is not None and time.monotonic() >= deadline:
        raise ObserverDeadlineExceeded("observer budget exhausted during " + stage)




def _show(unit: str, *properties: str) -> dict:
    args = ["systemctl", "--user", "show", unit]
    for name in properties:
        args += ["-p", name]
    text = subprocess.check_output(args, text=True, timeout=15)
    return boundary.unit_properties(text)


def check_pointer(home: Path, release: Path) -> Path:
    stable = rp.current_link(home)
    if not stable.is_symlink() or stable.resolve() != Path(release).resolve():
        raise ValueError(f"the stable pointer does not name this release: {stable.resolve()} != {Path(release).resolve()}")
    return stable


def check_units(home: Path, stable: Path, show=_show, *, sleep=time.sleep, retries: int = 3,
                reload=rp.reload_units, deadline=None) -> list[str]:
    """Every owned installed service is loaded as its file says; pinned ones are oneshot on the stable path."""
    checked = []

    def guarded(*args):
        _check_deadline(deadline, "unit inventory")
        return show(*args)
    expected = "PYTHONPATH=" + str(stable / "src")
    for unit_path in rp.config_paths(home):
        if unit_path.suffix != ".service" or "@." in unit_path.name:
            continue
        fields = guarded(unit_path.name, "Type", "Environment", "FragmentPath", "NeedDaemonReload", "LoadState")
        for _ in range(retries):
            # a concurrent sync may reload a moment later: look again before calling a pending reload a failure
            if fields.get("NeedDaemonReload") == "no":
                break
            sleep(2)
            fields = guarded(unit_path.name, "Type", "Environment", "FragmentPath", "NeedDaemonReload", "LoadState")
        if fields.get("NeedDaemonReload") != "no":
            # Another sync (a mind repairing generated-unit drift) rewrote the file after the activation's reload. A reload only
            # makes the loaded configuration equal to the files; what this probe judges is that the loaded pins equal the files
            # and the stable path, which is checked right after.
            reload()
            sleep(1)
            fields = guarded(unit_path.name, "Type", "Environment", "FragmentPath", "NeedDaemonReload", "LoadState")
        if fields.get("LoadState") != "loaded":
            raise ValueError(f"owned installed service is not loaded: {unit_path.name}")
        if fields.get("FragmentPath") != str(unit_path):
            raise ValueError(f"owned service fragment mismatch: {unit_path.name}")
        on_disk = boundary.unit_file_pins(unit_path.read_text())
        loaded = boundary.loaded_pins(fields.get("Environment", ""))
        if not on_disk and not loaded:
            continue
        if fields.get("Type") != "oneshot":
            raise ValueError(f"a pinned installed consumer is not a oneshot: {unit_path.name}")
        if fields.get("NeedDaemonReload") != "no":
            raise ValueError(f"pending daemon-reload: {unit_path.name}")
        if not loaded or set(loaded) != set(on_disk) or loaded != [expected]:
            raise ValueError(f"loaded pin differs from the unit file or the stable path: {unit_path.name}")
        checked.append(unit_path.name)
    return checked


def check_wrappers(home: Path, stable: Path, sha: str) -> None:
    for name in ("mishe", "mishe-tauftauf"):
        text = (Path(home) / "bin" / name).read_text()
        if str(stable) not in text or sha in text:
            raise ValueError(f"operator wrapper {name} does not name the stable pointer alone")


def drain_old(home: Path, cutoff_us: int, *, timeout: float = DRAIN_TIMEOUT_S, sleep=time.sleep,
              inventory=fence.inventory, pending_old=fence.pending_old, deadline=None) -> int:
    """Wait until no pinned consumer invocation that started at or before the cutoff is still running."""
    record = {"cutoff_us": cutoff_us, "before": {}}
    until = time.monotonic() + timeout
    if deadline is not None:
        until = min(until, deadline)
    own = os.environ.get("INVOCATION_ID", "")
    last_transient = None
    last_rows = None  # the most recent successful read that still held old invocations
    while True:
        try:
            pending = [row for row in pending_old(record, inventory(home)) if not (own and row["invocation"] == own)]
            if pending:
                last_rows = pending
        except ValueError as exc:
            # mid-start evidence is transient; anything else fails closed at once
            if not fence.is_transient(exc):
                raise
            pending, last_transient = [{"unit": "UNKNOWN", "error": str(exc)}], str(exc)
        if not pending:
            return 0
        if time.monotonic() >= until:
            # report the real pending rows and the last transient separately; neither masks the other
            raise ValueError("old consumer invocations remain: " + repr({
                "pending": pending, "last_observed_pending": last_rows, "last_transient": last_transient}))
        sleep(fence.RETRY_INTERVAL_S)


def wait_future(home: Path, expected: Path, *, timeout: float = DRAIN_TIMEOUT_S, sleep=time.sleep,
                deadline=None) -> list:
    """Wait for a stale active invocation; queued stale timers still fail immediately.

    A timer armed before the move can start its old service after the cutoff and
    therefore escape the timestamp drain. Never accept it while it is active.
    """
    until = time.monotonic() + timeout
    if deadline is not None:
        until = min(until, deadline)
    while True:
        try:
            return (fence.verify_future(home, expected, {}, deadline=deadline) if deadline is not None
                    else fence.verify_future(home, expected, {}))
        except fence.ActiveWrongPin:
            if time.monotonic() >= until:
                raise
            sleep(fence.RETRY_INTERVAL_S)


def observe(home: Path, release: Path, *, cutoff_us: int | None,
            budget: float = OBSERVE_BUDGET_S) -> dict:
    home, release = Path(home), Path(release)
    deadline = time.monotonic() + budget
    stable = check_pointer(home, release)
    sha = release.resolve().name
    units = check_units(home, stable, deadline=deadline)
    check_wrappers(home, stable, sha)
    problems = rp.probe_problems(home)
    if problems:
        raise ValueError("dual probe problems: " + "; ".join(map(str, problems)))
    _check_deadline(deadline, "fixed checks")
    rearm = None
    if cutoff_us is not None:
        drain_old(home, cutoff_us, deadline=deadline,
                  inventory=(lambda h: fence.inventory(h, deadline=deadline)))
        # ``arm.due`` re-arms the next-due timer when its handler exits, with the release that handler
        # runs. An old invocation that outlived the activation's own arm is therefore the last writer:
        # it left a pending timer whose PYTHONPATH names the previous release, and the future check
        # rolled the promotion back (2026-11-18 08:06Z, f8004457: "active or queued consumer has wrong
        # pin: mishe-next-due-c2a8ebf66195.service"). Drain first, then arm once more from this pin so
        # no old invocation remains to overwrite the timer, then judge the consumers.
        from .work_boundary import invoke_arm_consumer
        rearm = invoke_arm_consumer(stable / "src")
    future = (wait_future(home, stable / "src", deadline=deadline) if cutoff_us is not None
              else fence.verify_future(home, stable / "src", {}, deadline=deadline))
    return {"release": sha, "units": units, "rearm": rearm, "future_consumers": [row["unit"] for row in future]}


def main() -> int:
    home = os.environ.get("MISHE_SEED_HOME")
    if not home:
        raise ValueError("MISHE_SEED_HOME names no site")
    cutoff = os.environ.get("MISHE_PIN_CUTOFF_US", "")
    result = observe(Path(home), Path.cwd(), cutoff_us=int(cutoff) if cutoff.isdecimal() else None)
    print("functional consumer observation:", result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("pin observation failed:", exc, file=sys.stderr)
        sys.exit(1)
