"""Command line for the observation-driven runtime.

``wake``, ``yield``, ``signal``, ``inbox``, ``hold``, ``arm`` and ``due`` are
handled here; any other command falls through to the record/wall CLI.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

EXIT = {"unknown": 3, "failed": 4}
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def duration(text: str) -> float:
    if not text or text[-1] not in UNITS or not text[:-1].replace(".", "", 1).isdigit():
        raise argparse.ArgumentTypeError(f"duration {text!r} must look like 90s, 30m, 2h or 1d")
    return float(text[:-1]) * UNITS[text[-1]]


def default_home() -> Path:
    value = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    return Path(value) if value else Path(".mishe-tauftauf")


def declared_roles(home: Path) -> list[str]:
    return sorted(path.stem for path in (home / "charters").glob("*.md"))


def scheduler(home: Path):
    from . import arm as armmod
    """Real systemd timer when this site has installed units; otherwise record only."""
    def schedule(epoch):
        from .state_files import write_json
        write_json(home / "next-due.json", {"at": epoch})
        if (home / "units-installed").exists() and shutil.which("systemd-run"):
            armmod.systemd_scheduler(epoch, command=[sys.executable, "-m", "mishe_tauftauf",
                                                     "--home", str(home), "due"])
    return schedule


def parse_when(text: str) -> dict:
    if not text.startswith("signal:"):
        raise argparse.ArgumentTypeError("--when must look like signal:NAME=VALUE or signal:NAME@STATE")
    body = text[len("signal:"):]
    if "=" in body:
        name, value = body.split("=", 1)
        return {"signal": name, "equals": value}
    if "@" in body:
        name, state = body.split("@", 1)
        return {"signal": name, "state": state}
    raise argparse.ArgumentTypeError("--when needs =VALUE or @STATE")


def parser() -> argparse.ArgumentParser:
    from . import wake, yielding
    root = argparse.ArgumentParser(prog="mishe")
    root.add_argument("--home", type=Path, default=default_home())
    sub = root.add_subparsers(dest="command", required=True)
    p = sub.add_parser("wake"); p.add_argument("role"); p.add_argument("--timeout", type=float, default=wake.DEFAULT_TIMEOUT)
    p.add_argument("--repo", type=Path, default=os.environ.get("MISHE_REPO"), help="engine working directory (default: MISHE_REPO)")
    p = sub.add_parser("yield"); p.add_argument("--result", choices=yielding.RESULTS, required=True)
    p.add_argument("--continue", dest="continue_", action="store_true")
    p.add_argument("--again", type=duration); p.add_argument("--when", type=parse_when)
    p.add_argument("--file", type=Path, help="wall text file (default: stdin)")
    sig = sub.add_parser("signal").add_subparsers(dest="signal_command", required=True)
    p = sig.add_parser("register"); p.add_argument("name"); p.add_argument("--max-age", type=float, required=True)
    p.add_argument("--subscribers", default="")
    p = sig.add_parser("observe"); p.add_argument("name"); p.add_argument("value"); p.add_argument("--source", default="cli")
    p = sig.add_parser("show"); p.add_argument("name")
    inbox = sub.add_parser("inbox").add_subparsers(dest="inbox_command", required=True)
    p = inbox.add_parser("touch"); p.add_argument("role")
    hold = sub.add_parser("hold"); hold.add_argument("state", choices=("on", "off", "status"))
    hold.add_argument("--reason", default="held")
    sub.add_parser("arm"); sub.add_parser("due")
    return root


def run(args) -> int:
    from . import arm as armmod
    from . import engines, owed, signal_store, wake, yielding
    home = args.home.resolve()
    now = time.time()
    if args.command == "signal":
        if args.signal_command == "register":
            signal_store.register(home, args.name, max_age=args.max_age,
                                  subscribers=[s for s in args.subscribers.split(",") if s])
        elif args.signal_command == "observe":
            edge = signal_store.observe(home, args.name, args.value, now=now, source=args.source)
            armmod.arm(home, scheduler=scheduler(home))
            print("edge" if edge else "refreshed")
        else:
            print(signal_store.read(home, args.name, now=now))
        return 0
    if args.command == "inbox":
        owed.touch_inbox(home, args.role)
        return 0
    if args.command == "hold":
        path = home / "hold"
        if args.state == "on":
            path.write_text(args.reason + "\n")
        elif args.state == "off":
            path.unlink(missing_ok=True)
        print("held" if path.exists() else "running")
        return 0
    if args.command == "arm":
        print(armmod.arm(home, scheduler=scheduler(home)))
        return 0
    if args.command == "due":
        roles = declared_roles(home)
        print(armmod.due(home, now=now, scheduler=scheduler(home), roles=roles, starter=wake.systemd_starter))
        return 0
    if args.command == "yield":
        role, key = os.environ.get("MISHE_ROLE"), os.environ.get("MISHE_WAKE_KEY")
        if not role or not key:
            print("mishe: yield only works inside a wake (MISHE_ROLE and MISHE_WAKE_KEY are unset)", file=sys.stderr)
            return 2
        text = args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
        message = yielding.settle(home, role, key, text=text, result=args.result, now=now,
                                  continue_=args.continue_, again=args.again, when=args.when)
        armmod.arm(home, scheduler=scheduler(home))
        print(message)
        return 0
    role = args.role                                   # wake
    result = wake.run_wake(home, role, roles=declared_roles(home), engine=engines.for_role(home, role),
                           now=now, timeout=args.timeout, repo=args.repo)
    print(f"wake {role}: {result.outcome}" + (f" ({result.detail})" if result.detail else ""))
    return EXIT.get(result.outcome, 0)


RUNTIME_COMMANDS = {"wake", "yield", "signal", "inbox", "hold", "arm", "due"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    words = [a for a in argv if not a.startswith("-")]
    # the first non-option word that is a known command picks the runtime CLI
    command = next((w for w in words if w in RUNTIME_COMMANDS), None)
    if command is None or words.index(command) > 1:
        from . import cli
        return cli.main(argv)
    from .core.supervisor import SupervisorError
    try:
        return run(parser().parse_args(argv))
    except (OSError, ValueError, SupervisorError) as exc:
        print(f"mishe: {exc}", file=sys.stderr)
        return 2
