"""Command line for the observation-driven runtime, registered into the one ``mishe`` parser."""
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


def default_pythonpath() -> str:
    """The runtime the installed wake unit runs, else this checkout's src.

    Generated units pin PYTHONPATH to the release they were rendered from, so a
    plain ``doctor`` run from the working tree must compare against that pin,
    not against whatever checkout the command happens to import from.
    """
    from .revive_boundary import pinned_path
    try:
        text = (Path.home() / ".config" / "systemd" / "user" / "mishe-wake@.service").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    return pinned_path(text) or str(Path(__file__).resolve().parents[1])


def declared_roles(home: Path) -> list[str]:
    from .roles import declared
    return sorted(declared(home))


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


WAKE_TIMEOUT = 1800.0                      # equals wake.DEFAULT_TIMEOUT (pinned by a test)
YIELD_RESULTS = ("changed", "verified", "blocked")   # equals yielding.RESULTS (pinned by a test)


def register(sub) -> None:
    """Add the runtime commands to the ``mishe`` parser. Heavy modules load only when a command runs."""
    def add(*args, **kwargs):
        p = sub.add_parser(*args, **kwargs)
        p.set_defaults(func=cmd_runtime)
        return p
    p = add("wake"); p.add_argument("role"); p.add_argument("--timeout", type=float, default=WAKE_TIMEOUT)
    p.add_argument("--repo", type=Path, default=os.environ.get("MISHE_REPO"), help="engine working directory (default: MISHE_REPO)")
    p = add("yield"); p.add_argument("--result", choices=YIELD_RESULTS, required=True)
    p.add_argument("--continue", dest="continue_", action="store_true")
    p.add_argument("--again", type=duration); p.add_argument("--when", type=parse_when)
    p.add_argument("--file", type=Path, help="wall text file (default: stdin)")
    p.add_argument("--work-file", type=Path)
    p.add_argument("--artifact", type=Path, action="append", default=[])
    work = add("work").add_subparsers(dest="work_command", required=True)
    p = work.add_parser("show"); p.set_defaults(func=cmd_runtime); p.add_argument("role")
    p = work.add_parser("set"); p.set_defaults(func=cmd_runtime); p.add_argument("--file", type=Path, required=True)
    p.add_argument("--role", help="explicit operator target; residents may edit only their own role")
    p = work.add_parser("report"); p.set_defaults(func=cmd_runtime)
    p.add_argument("--start", required=True); p.add_argument("--end", required=True); p.add_argument("--output", type=Path, required=True)
    sig = add("signal").add_subparsers(dest="signal_command", required=True)
    p = sig.add_parser("register"); p.set_defaults(func=cmd_runtime); p.add_argument("name"); p.add_argument("--max-age", type=float, required=True)
    p.add_argument("--subscribers", default="")
    p = sig.add_parser("observe"); p.set_defaults(func=cmd_runtime); p.add_argument("name"); p.add_argument("value"); p.add_argument("--source", default="cli")
    p = sig.add_parser("show"); p.set_defaults(func=cmd_runtime); p.add_argument("name")
    p = sig.add_parser("run", help="observe NAME by running CMD: exit 0 GREEN, non-zero RED, cannot-run leaves it to expire")
    p.add_argument("name"); p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--red-codes", default="2", help="exit codes that mean RED (default 2)")
    p.set_defaults(cmd=[])
    p = add("produce"); p.add_argument("name")
    inbox = add("inbox").add_subparsers(dest="inbox_command", required=True)
    p = inbox.add_parser("touch"); p.set_defaults(func=cmd_runtime); p.add_argument("role")
    hold = add("hold"); hold.add_argument("state", choices=("on", "off", "status"))
    hold.add_argument("--reason", default="held")
    add("arm"); add("due"); add("migrate")
    p = add("failed"); p.add_argument("unit")
    for name in ("sync", "doctor"):
        p = add(name)
        p.add_argument("--unit-dir", type=Path, default=Path.home() / ".config" / "systemd" / "user")
        p.add_argument("--prefix", default="mishe")
        p.add_argument("--no-slices", action="store_true", help="scratch namespaces: no host slices")
        p.add_argument("--pythonpath", default=default_pythonpath())
        p.add_argument("--repo", type=Path, default=os.environ.get("MISHE_REPO") or Path.cwd())
        if name == "sync":
            p.add_argument("--no-systemctl", action="store_true")
            p.add_argument("--no-enable", action="store_true", help="install the units but enable and start no trigger")
            p.add_argument("--uninstall", action="store_true", help="remove the generated units (and only those)")


def run(args) -> int:
    from . import arm as armmod
    from . import engines, owed, signal_store, wake, yielding
    home = args.home.resolve()
    now = time.time()
    if args.command == "work":
        from . import work_state, work_metrics
        from .state_files import read_json, write_json
        import json
        if args.work_command == "show":
            print(json.dumps(work_state.load(home, args.role), indent=2))
        elif args.work_command == "set":
            record = read_json(args.file)
            resident = os.environ.get("MISHE_ROLE")
            role = resident or args.role
            if not role or record.get("role") != role or (resident and args.role and args.role != resident):
                raise ValueError("work role must match current wake or explicit operator --role")
            work_state.save(home, role, record)
        else:
            from datetime import datetime
            def epoch(value):
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None: raise ValueError("report timestamps need timezone")
                return dt.timestamp()
            result = work_metrics.report(home, start=epoch(args.start), end=epoch(args.end))
            if not args.output.resolve().is_relative_to(home): raise ValueError("output must be under SITE_HOME")
            write_json(args.output, result)
            print(f"{result['wakes']} wakes; {len(result['attributed_commits'])} attributed commits; missing usage={result['missing_usage']}")
        return 0
    if args.command == "signal":
        if args.signal_command == "register":
            signal_store.register(home, args.name, max_age=args.max_age,
                                  subscribers=[s for s in args.subscribers.split(",") if s])
        elif args.signal_command == "observe":
            edge = signal_store.observe(home, args.name, args.value, now=now, source=args.source)
            armmod.arm(home, scheduler=scheduler(home))
            print("edge" if edge else "refreshed")
        elif args.signal_command == "run":
            from . import producers
            command = args.cmd
            if not command:
                raise ValueError("signal run needs a command after --")
            code = producers.run(home, args.name, command, now=now, timeout=args.timeout,
                                red_codes=tuple(int(c) for c in args.red_codes.split(",") if c != "none"))
            armmod.arm(home, scheduler=scheduler(home))
            return code
        else:
            print(signal_store.read(home, args.name, now=now))
        return 0
    if args.command == "produce":
        from . import producers
        try:
            print(producers.produce(home, args.name, now=now))
        except producers.ObservationFailed as exc:
            print(f"mishe: {args.name}: observation failed: {exc}", file=sys.stderr)
            return 2
        armmod.arm(home, scheduler=scheduler(home))
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
            following = wake.next_role(home, declared_roles(home), now=now)
            if following:                    # wakes that arrived while held would not otherwise return
                wake.systemd_starter(following)
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
        from .state_files import read_json
        text = args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
        message = yielding.settle(home, role, key, text=text, result=args.result, now=now,
                                  continue_=args.continue_, again=args.again, when=args.when,
                                  artifacts=args.artifact, work_record=read_json(args.work_file) if args.work_file else None)
        armmod.arm(home, scheduler=scheduler(home))
        print(message)
        return 0
    if args.command == "migrate":
        import json
        from . import migrate
        print(json.dumps(migrate.apply(home, declared_roles(home), now=now), sort_keys=True))
        return 0
    if args.command == "failed":
        from . import failure
        print(failure.report(home, args.unit, now=now))
        return 0
    if args.command in ("sync", "doctor"):
        return run_units(args, home, now)
    role = args.role                                   # wake
    result = wake.run_wake(home, role, roles=declared_roles(home), engine=engines.for_role(home, role),
                           now=now, timeout=args.timeout, repo=args.repo, lock_wait=args.timeout)
    print(f"wake {role}: {result.outcome}" + (f" ({result.detail})" if result.detail else ""))
    return EXIT.get(result.outcome, 0)


def run_units(args, home: Path, now: float) -> int:
    import subprocess
    from . import signal_store, units
    if not home.is_dir():                   # before anything writes: a bogus home must stay uncreated
        raise ValueError(f"site home does not exist: {home}")
    cfg = units.Config(home=home, repo=Path(args.repo).resolve(), python=sys.executable,
                       pythonpath=args.pythonpath, unit_dir=args.unit_dir, prefix=args.prefix,
                       **({"plants_slice": None, "lifeline_slice": None} if args.no_slices else {}))
    roles = declared_roles(home)
    from . import producers
    from . import duties
    from .state_files import write_json
    declared = producers.load(home, python=sys.executable, declared=duties.declared(cfg.repo, home))
    declared += producers.load(home, python=sys.executable)
    write_json(home / "resolvers.generated.json", duties.resolvers(cfg.repo, args.prefix))
    if args.command == "sync":
        runner = None if args.no_systemctl else (lambda c: subprocess.run(c, check=True))
        report = units.sync(cfg, roles, producers=declared, runner=runner, uninstall=args.uninstall,
                            enable=not args.no_enable)
        print(f"written {len(report.written)}, removed {len(report.removed)}")
        return 0
    report = units.doctor(cfg, roles, producers=declared)
    for duty in duties.load(cfg.repo):
        if duty["resolver"] not in roles:
            report.state = "RED"
            report.problems.append(f"duty {duty['name']}: resolver role {duty['resolver']!r} has no charter")
    top = subprocess.run(["git", "-C", str(home.parent), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if top.returncode == 0 and Path(top.stdout.strip()).resolve() == home.parent:
        tracked = subprocess.run(["git", "-C", str(home.parent), "ls-files", "--", home.name],
                                 capture_output=True, text=True)
        if tracked.returncode or tracked.stdout.strip():
            report.state = "RED"
            report.problems.append("HOLD local-plant-in-git: remove staged or tracked site files from the index")
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


def command_tail(argv: list) -> tuple[list, list]:
    """Split off everything after ``--`` (a command to run), untouched by argparse."""
    if "--" in argv:
        cut = argv.index("--")
        return argv[:cut], argv[cut + 1:]
    return argv, []


def cmd_runtime(args) -> int:
    from .core.supervisor import SupervisorError
    try:
        return run(args)
    except (OSError, ValueError, SupervisorError) as exc:
        print(f"mishe: {exc}", file=sys.stderr)
        return 2
