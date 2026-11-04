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
    p = sub.add_parser("failed"); p.add_argument("unit")
    for name in ("sync", "doctor"):
        p = sub.add_parser(name)
        p.add_argument("--unit-dir", type=Path, default=Path.home() / ".config" / "systemd" / "user")
        p.add_argument("--prefix", default="mishe")
        p.add_argument("--no-slices", action="store_true", help="scratch namespaces: no host slices")
        p.add_argument("--pythonpath", default=str(Path(__file__).resolve().parents[1]))
        p.add_argument("--repo", type=Path, default=os.environ.get("MISHE_REPO") or Path.cwd())
        if name == "sync":
            p.add_argument("--no-systemctl", action="store_true")
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
    if args.command == "failed":
        from . import failure
        print(failure.report(home, args.unit, now=now))
        return 0
    if args.command in ("sync", "doctor"):
        return run_units(args, home, now)
    role = args.role                                   # wake
    result = wake.run_wake(home, role, roles=declared_roles(home), engine=engines.for_role(home, role),
                           now=now, timeout=args.timeout, repo=args.repo)
    print(f"wake {role}: {result.outcome}" + (f" ({result.detail})" if result.detail else ""))
    return EXIT.get(result.outcome, 0)


def run_units(args, home: Path, now: float) -> int:
    import subprocess
    from . import signal_store, units
    cfg = units.Config(home=home, repo=Path(args.repo).resolve(), python=sys.executable,
                       pythonpath=args.pythonpath, unit_dir=args.unit_dir, prefix=args.prefix,
                       **({"plants_slice": None, "lifeline_slice": None} if args.no_slices else {}))
    roles = declared_roles(home)
    if args.command == "sync":
        runner = None if args.no_systemctl else (lambda c: subprocess.run(c, check=True))
        report = units.sync(cfg, roles, runner=runner)
        print(f"written {len(report.written)}, removed {len(report.removed)}")
        return 0
    if not home.is_dir():
        raise ValueError(f"site home does not exist: {home}")
    report = units.doctor(cfg, roles)
    top = subprocess.run(["git", "-C", str(home.parent), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if top.returncode == 0 and Path(top.stdout.strip()).resolve() == home.parent:
        tracked = subprocess.run(["git", "-C", str(home.parent), "ls-files", "--", home.name],
                                 capture_output=True, text=True)
        if tracked.returncode or tracked.stdout.strip():
            report.state = "RED"
            report.problems.append("local-plant-in-git: remove staged or tracked site files from the index")
        else:
            print("PASS local plant out of Git")
    if (home / "chat.log").exists():                   # the tape is the record; unreadable means not GREEN
        from .feed import Feed, FeedError
        try:
            Feed(home).entries()
        except FeedError as exc:
            report.state = "RED"
            report.problems.append(f"chat.log: feed-corrupt: {exc}")
    if "doctor" in signal_store.registry(home):
        # The meta-signal: a doctor that stops running ages out to UNKNOWN on schedule.
        signal_store.observe(home, "doctor", "GREEN" if report.state == "GREEN" else "RED",
                             now=now, source="doctor")
        armmod_arm(home)
    print(report.state)
    for problem in report.problems:
        print(f"  {problem}")
    return {"GREEN": 0, "RED": 1}.get(report.state, 3)


def armmod_arm(home: Path) -> None:
    from . import arm as armmod
    armmod.arm(home, scheduler=scheduler(home))


RUNTIME_COMMANDS = {"wake", "yield", "signal", "inbox", "hold", "arm", "due", "failed", "sync", "doctor"}


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
