"""Operator commands for the root containment guard: install, restore, revert.

    mishe-tauftauf guard install   corrected guard in place, sentinel restarted, activation verified
    mishe-tauftauf guard restore   once the guard's own readiness gate passes, start the restore unit
    mishe-tauftauf guard revert    previous guard back, sentinel restarted, activation verified

Safe for a mind to run on its own: each command composes the reviewed tools
(tools/guard-activation/, checks/restore-readiness.py), decides only from exit
codes, and keeps every gate. Restore refuses unless the readiness detector says
READY. Install and revert refuse unless the plant is already latched, because a
sentinel restart latches workloads off; there is deliberately no flag that
stops a running plant. Every attempt leaves a receipt in SITE/operator/guard-actions.jsonl.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE = Path("/var/lib/mishe-containment")
SENTINEL = "mishe-containment.service"
RESTORE_UNIT = "mishe-restore-2147b.service"
RESTORE_WAIT = 150


def _run(argv, *, sudo=False):
    argv = (["sudo", "-n"] if sudo else []) + [str(a) for a in argv]
    print("+ " + " ".join(argv), flush=True)
    return subprocess.run(argv).returncode


def latched() -> bool:
    return (STATE / "tripped").exists()


def _receipt(home: Path, action: str, code: int, note: str) -> None:
    row = {"at": time.time(), "action": action, "exit": code, "by": os.environ.get("MISHE_SEED_ROLE", "operator"), "note": note}
    try:
        path = home / "operator" / "guard-actions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as exc:  # never silent, never fatal to the action
        print(f"warning: receipt not written: {exc}", file=sys.stderr)


def _swap(repo: Path, action: str) -> tuple[int, str]:
    tools = repo / "tools" / "guard-activation"
    if not latched():
        return 2, "refused: the plant is running and a sentinel restart latches workloads off; run this while latched"
    if _run([sys.executable, tools / "apply.py", *([] if action == "install" else ["revert"])], sudo=True):
        return 1, "guard bytes unchanged (apply.py refused or failed); sentinel not restarted"
    if _run(["systemctl", "restart", SENTINEL], sudo=True):
        return 1, "bytes changed but sentinel restart failed; not running them"
    time.sleep(3)
    if _run([sys.executable, tools / "check-activation.py"]):
        return 1, "sentinel is not yet running the installed bytes; inspect check-activation.py"
    return 0, f"guard {action}: installed, sentinel restarted, new bytes running" + ("; next: guard restore" if action == "install" else "")


def _restore(repo: Path) -> tuple[int, str]:
    if not latched():
        return 0, "nothing to restore: no containment latch present"
    if _run([sys.executable, repo / "checks" / "restore-readiness.py"]):
        return 2, "refused: readiness gate not satisfied (output above); nothing started"
    if _run(["systemctl", "--user", "start", RESTORE_UNIT]):
        return 1, "restore unit failed to start; latch unchanged"
    deadline = time.time() + RESTORE_WAIT
    while latched() and time.time() < deadline:
        time.sleep(3)
    if latched():
        return 1, f"restore started, latch still present after {RESTORE_WAIT}s; systemctl --user status {RESTORE_UNIT}"
    return 0, "latch cleared; confirm with checks/claude-gate.py and guard.py status"


def cmd_guard(args) -> int:
    home = Path(args.home).resolve()
    repo = home.parent
    code, note = _restore(repo) if args.guard_command == "restore" else _swap(repo, args.guard_command)
    print(note)
    _receipt(home, args.guard_command, code, note)
    return code


def register(sub):
    parser = sub.add_parser("guard", help="Install, restore or revert the root containment guard")
    cmd = parser.add_subparsers(dest="guard_command", required=True)
    for name, text in (("install", "Install the corrected guard and activate it (while latched)"),
                       ("restore", "Restore the plant from the latch once the guard's gate is satisfied"),
                       ("revert", "Put the previous guard back and activate it (while latched)")):
        cmd.add_parser(name, help=text).set_defaults(func=cmd_guard)
