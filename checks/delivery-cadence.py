#!/usr/bin/env python3
"""Producer: the automatic moving-pin cadence, defined in the repository.

Keep the moving pin close to main by starting one small promotion whenever the pin
trails a settled HEAD (operator, 2026-11-14: "deliver as often as possible"). This
logic used to live in a hand-written ``cadence.sh`` under the site's ``operator/``
area, driven by a hand-installed ``mishe-delivery-cadence.timer``: ``mishe sync``
neither generated nor observed it, so a stalled cadence produced silence. Declared
as the ``delivery-cadence`` duty, the timer is generated from ``duties.json`` and
the ``delivery-cadence`` signal ages to UNKNOWN if the cadence stops running.

Starts one promotion when all hold:
  - no promotion, heal or patch gate is already running;
  - the pin trails HEAD and the newest commit is older than SETTLE_S (minds done);
  - HEAD is outside the retry bound, or the latest launch has a no-effect refusal.

Remote CI is not a condition: the promotion check runs the whole suite on the exact
bytes in a clean worktree, so CI is only logged here. This script never edits the
gate; it only starts the installed promotion, which re-targets, checks, reviews,
applies and verifies.

Exit: 0 after an observation, 3 for an uncertain launch. A skip and a started promotion both read as a healthy
run; the signal is GREEN while the cadence keeps running and ages to UNKNOWN if it
stops, which wakes its resolver.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SETTLE_S = 60
"""The newest commit must be at least this old before a promotion starts (minds done with it)."""

RETRY_S = 10800
"""Bound between attempts without a current no-effect refusal."""


def _newest_promotion_refused(home: Path, head: str, attempted_at: float, now: float) -> bool:
    """A recorded delivery-busy outcome can authorize a retry of the latest launch.

    A launch can end before it writes a promotion record. Use the record's creation
    time so a refusal predating that launch cannot authorize another retry.
    """
    directory = home / "work" / "releases"
    try:
        newest = max(directory.glob(f"{head}-*.json"), key=lambda path: path.stat().st_mtime, default=None)
        if newest is None:
            return False
        record = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict):
        return False
    created_at = record.get("created_at")
    return (record.get("sha") == head and record.get("phase") == "delivery-busy"
            and isinstance(created_at, (int, float)) and attempted_at <= created_at <= now)


RUNNING_UNITS = ("moving-pin-delivery-*", "genome-deliver-*")
"""Transient units a promotion or heal runs as; one running means the cadence must not start another."""

RUNNING_PROCESSES = (
    r"^(/bin/)?bash [^ ]*delivery/(deliver|heal).sh",
    r"^[^ ]*python[0-9.]* -m mishe_tauftauf.wall_patch",
    # ``mishe-promote``'s ``promote-head.sh`` execs ``release advance``, so a promotion
    # started outside its unit (a hand-run ``mishe-deliver``) is still seen. A delivery
    # request consumed by another role's wake does NOT look like this: it runs the
    # promotion in-process in the consumer's own CLI (measured 2026-11-18: record
    # 56262f54-1794982435362421925 was created by ``mishe-wake@genome.service``, whose
    # argv is ``wake``), which is what ``_admission_held`` covers.
    r"^[^ ]*python[0-9.]* -m mishe_tauftauf release advance",
)
"""Legacy delivery/heal scripts, the patch gate and ``release advance``, matched by argv like the retired ``cadence.sh``."""


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] {message}")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def pin_sha(home: Path) -> str:
    """The commit the pointer names, or "" when ``runtime/current`` is missing or unreadable."""
    try:
        return Path(os.path.realpath(home / "runtime" / "current")).name
    except OSError:
        return ""


def _running(cmd: list) -> bool:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and bool(done.stdout)


def _units_running() -> bool:
    units = subprocess.run(
        ["systemctl", "--user", "list-units", "--no-legend", "--state=activating,active,running", *RUNNING_UNITS],
        capture_output=True, text=True)
    return units.returncode == 0 and bool(units.stdout)


def _admission_held(home: Path) -> bool:
    """True while a consumed delivery request holds delivery admission.

    ``delivery_requests.consume`` writes ``work/delivery-admission.json`` before it
    starts validating and promoting and removes it when the attempt ends, so the
    marker exists for a request-consumed promotion's whole check stage -- the stage
    that runs before any dispatch lock. Measured 2026-11-18 06:13:55Z: the record
    ``56262f54-1794982435362421925`` (request 8948d803…) was started by
    ``mishe-wake@genome.service`` and its suite ran beside the cadence's 632f743e
    promotion, which started 54 s later. A marker whose owner died is released by
    the next consume pass (``ADMISSION_MAX_AGE_SECONDS``), so this can only make the
    cadence skip, never wedge it.
    """
    return (Path(home) / "work" / "delivery-admission.json").exists()


def gate_running(home) -> bool:
    """True when a delivery, request promotion or patch gate is already running, so the cadence must skip."""
    if _admission_held(Path(home)):
        return True
    for pattern in RUNNING_PROCESSES:
        if _running(["pgrep", "-f", pattern]):
            return True
    return _units_running()


def should_start(pin: str, head: str, newest_at: float, last: tuple | None, now: float,
                 *, settle: int = SETTLE_S, retry: int = RETRY_S, refused: bool = False) -> tuple:
    """``(start, reason)`` for the pure timing gate, so the decision is testable without a delivery.

    ``pin``/``head`` are commit sha strings, ``newest_at`` the HEAD commit time, ``last`` the
    ``(sha, attempted_at)`` last written or None, and ``now`` the current epoch seconds.
    ``refused`` marks a delivery-busy outcome for HEAD: that launch had no effect, so the
    retry bound does not apply and the next tick may start a fresh promotion.
    """
    if not pin or not head:
        return False, "no pin or HEAD to compare"
    if pin == head:
        return False, "pin is HEAD: nothing to do"
    if now - newest_at < settle:
        return False, f"newest commit is {int(now - newest_at)}s old (< {settle}s): wait"
    if last is not None and last[0] == head and not refused and 0 <= now - last[1] < retry:
        return False, f"{head[:8]} was attempted {int((now - last[1]) / 60)} min ago: wait for HEAD to move"
    return True, "deliver"


def _read_state(home: Path):
    try:
        fields = (home / "delivery-cadence.last").read_text(encoding="utf-8").split()
        if len(fields) not in (2, 3) or (len(fields) == 3 and fields[2] not in ("unknown", "started")):
            return None
        return fields[0], float(fields[1]), fields[2] if len(fields) == 3 else "started"
    except (OSError, ValueError):
        return None


def _remote_ci(repo: Path, head: str) -> str:
    try:
        done = subprocess.run(["gh", "run", "list", "-L", "10", "--json", "headSha,status,conclusion"],
                              cwd=str(repo), capture_output=True, text=True, timeout=30)
        for run in json.loads(done.stdout or "[]"):
            if run.get("headSha") == head:
                return f"{run.get('status')}:{run.get('conclusion')}"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return "unknown"


def run(home: Path, repo: Path, *, now: float | None = None) -> int:
    tick_at = time.time() if now is None else now
    if os.environ.get("MISHE_WAKE_KEY"):
        _log("inside a wake: skip")
        return 0
    if gate_running(home):
        _log("a delivery, request promotion or gate is running (admission held): skip")
        return 0
    pin = pin_sha(home)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # Promote only commits origin/main holds. The promotion takes the shared checkout's HEAD, so an
    # unpushed commit whose push is then rejected leaves the pin naming a commit main never contained
    # (2026-11-22: pin 406dffc7 was reset away by a non-fast-forward push).
    on_main = _git(repo, "merge-base", "--is-ancestor", head, "origin/main")
    if on_main.returncode == 1:
        _log(f"HEAD {head[:8]} is not on origin/main yet: not promoting an unpushed commit")
        return 0
    if on_main.returncode != 0:
        _log(f"origin/main is unreadable (rc {on_main.returncode}): promotion outcome unknown")
        return 3
    newest = _git(repo, "log", "-1", "--format=%ct", head).stdout.strip()
    last = _read_state(home)
    refused = bool(last and last[0] == head and last[2] == "started"
                   and _newest_promotion_refused(home, head, last[1], tick_at))
    start, reason = should_start(pin, head, float(newest or 0), last, tick_at, refused=refused)
    if not start:
        _log(reason)
        if pin != head and last and last[0] == head and last[2] == "unknown":
            _log("previous launch outcome remains unknown")
            return 3
        return 0
    # A pin rewritten away by an amend is not an ancestor of HEAD; the promotion re-targets from
    # the merge base, so only note it.
    if _git(repo, "merge-base", "--is-ancestor", pin, head).returncode != 0:
        _log(f"pin {pin[:8]} is not an ancestor of HEAD (amended away): delivering from the merge base")
    _log(f"remote CI on {head[:8]}: {_remote_ci(repo, head)} (signal only)")
    base = _git(repo, "merge-base", pin, head).stdout.strip() or pin
    count = _git(repo, "rev-list", "--count", f"{base}..{head}").stdout.strip()
    state_path = home / "delivery-cadence.last"
    try:
        previous = state_path.read_bytes()
    except FileNotFoundError:
        previous = None
    attempted_at = time.time() if now is None else now
    state_path.write_text(f"{head} {attempted_at} unknown\n", encoding="utf-8")
    _log(f"pin {pin[:8]} -> HEAD {head[:8]}, {count or '?'} commits, delivering")
    env = {**os.environ, "MISHE_SEED_HOME": str(home)}
    env.pop("MISHE_WAKE_KEY", None)
    env.pop("MISHE_ROLE", None)
    try:
        done = subprocess.run([str(home / "bin" / "mishe-deliver")], env=env,
                              capture_output=True, text=True, timeout=120)
    except OSError as exc:
        _log(f"could not start the promotion: {type(exc).__name__}: {exc}")
        if previous is None:
            state_path.unlink()
        else:
            state_path.write_bytes(previous)
        return 3
    except subprocess.SubprocessError as exc:
        _log(f"promotion launch outcome unknown: {type(exc).__name__}: {exc}")
        return 3
    for line in (done.stdout + done.stderr).splitlines()[-2:]:
        _log(line)
    if done.returncode != 0:
        _log(f"promotion launch exited {done.returncode}; outcome unknown")
        return 3
    state_path.write_text(f"{head} {attempted_at} started\n", encoding="utf-8")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start one moving-pin promotion when the pin trails a settled HEAD.")
    parser.add_argument("--home", type=Path, required=True, help="the site home")
    parser.add_argument("--repo", type=Path, required=True, help="the shared repository")
    args = parser.parse_args(argv)
    return run(args.home, args.repo)


if __name__ == "__main__":
    sys.exit(main())
