from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .checks import HealthPublishBusy, run_check
from . import access, discovery
from .feed import Feed, FeedError, parse_feed, utc_now
from .judges import QUESTIONS, classify, controls, document, run_external

from .observations import (FOOTER_WAKE_LABEL, compose_frame, discover, executable,
                           validate_home, validate_home_binding, validate_slug)
from .predictions import PredictionError, append_prediction, pending_predictions, replay_predictions


def default_home() -> Path:
    """Resolve the site home the same way resident minds are told to name it.

    seed.py exports MISHE_SEED_HOME to launched minds and the restore text points them
    there, but this function read a different variable (MISHE_TAUFTAUF_HOME), so the
    exported name was inert and a mind without it retyped a plausible-looking relative
    path. Prefer the canonical site variable, then the workspace-local fallback.
    """
    return Path(os.environ.get("MISHE_SEED_HOME")
                or os.environ.get("MISHE_TAUFTAUF_HOME", ".mishe-tauftauf"))



def initialize(home: Path) -> None:
    from .wall import settings
    settings(home)  # Refuse an explicitly retired coordination mode before mutation.
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / "coordination-mode.json"
    try:
        with config_path.open("x", encoding="utf-8") as stream:
            json.dump({"started":utc_now(), "until":None}, stream)
            stream.write("\n")
    except FileExistsError:
        pass
    for name in ("top-pains", "minds", "observations", "filters", "projectors", "handoffs", "plans"):
        (home / name).mkdir(exist_ok=True)
    if not (home / "feed").exists():
        (home / "chat.log").touch(mode=0o600, exist_ok=True)


def _require_worktree(home: Path) -> None:
    """A site is planted directly inside a Git worktree, never as a subtree of another directory."""
    if (home.resolve() / ".git").exists():
        raise ValueError(f"site cannot be a repository root: {home}")
    workspace = home.parent.resolve()
    if not workspace.is_dir():
        raise ValueError(f"site must be directly inside a Git worktree: {workspace}")
    repository = subprocess.run(["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True)
    if repository.returncode or Path(repository.stdout.strip()).resolve() != workspace:
        raise ValueError(f"site must be directly inside a Git worktree: {workspace}")


def cmd_init(args) -> int:
    _require_worktree(args.home)
    initialize(args.home)
    from .roles import install_defaults
    install_defaults(args.home)
    print(args.home)
    return 0


def cmd_append(args) -> int:
    text = args.text if args.text is not None else sys.stdin.read()
    from .seed_board import STATE_RE
    from .task_state import states
    first = text.splitlines()[0].lstrip() if text else ""
    if match := STATE_RE.match(first):
        state = states(Feed(args.home).entries()).get(match.group(2))
        if state and match.group(1) in {"done", "dropped"}:
            if args.source not in {state.owner, "operator"}:
                raise ValueError(f"task belongs to {state.owner}; finish only your child step")
            if state.managed:
                raise ValueError("managed task completion requires task finish with checked result and evidence")
    entry = Feed(args.home).append(args.source, text)
    from .owed import touch_inbox
    from .wall_text import dm_recipient
    from .roles import declared
    if recipient := dm_recipient(text, args.source, declared(args.home)):
        touch_inbox(args.home, recipient)     # an addressed DM owes its recipient a wake, as wall dm does
    print(entry.sequence)
    return 0


def _staging_anchor(home: Path, role: str, raw: Path) -> Path | None:
    """The role's own staging anchor a bare ``--file`` name resolves to, or None.

    Returns the anchor only for a bare name (no directory component, not
    absolute) whose per-role file exists; every other argument is not an anchor
    and keeps its own meaning. Callers that need to know whether the notes they
    are about to consume came from the anchor (rather than a deliberate path)
    ask here instead of re-deriving it from the path text.
    """
    if raw.is_absolute() or len(raw.parts) > 1:
        return None
    anchor = home / "walls" / f"{role}-{raw.name}"
    return anchor if anchor.is_file() else None


def _staging_notes(home: Path, role: str, raw: Path) -> Path:
    """Resolve a relative notes argument to the role's own staging anchor.

    ``wall write``/``wall dm``/``wall outcome`` and ``seed yield`` read the
    notes a role staged for that command. A bare name (``NOTES``, ``MESSAGE``)
    resolves to ``SITE_HOME/walls/<role>-<name>`` — a per-role anchor, so one
    role can never read another role's staged notes. The earlier shared
    ``walls/NOTES`` anchor was the cross-role contamination vector that put
    genome's staged content on docs' wall (2026-10-11, audit digest 5fd23b39).
    Without the anchor the command fails closed instead of silently reading a
    same-named file from the shared directory or the working directory. A path
    with a directory component (``artifacts/…``, ``walls/…``) keeps its
    working-directory meaning, and an absolute path is never re-anchored.
    """
    if raw.is_absolute() or len(raw.parts) > 1:
        return raw
    anchor = _staging_anchor(home, role, raw)
    if anchor is not None:
        return anchor
    raise ValueError(
        f"no staged notes for role '{role}': expected {home / 'walls' / f'{role}-{raw.name}'} "
        f"(or pass an explicit relative or absolute path)"
    )
def _require_site_role(home: Path, role: str) -> None:
    """Reject wall mutations attributed to a role not registered by this site."""
    from .roles import declared

    if role not in declared(home):
        raise ValueError(f"role '{role}' is not registered for site home {home}")

def _require_linked_site(home: Path, site_name: str) -> None:
    """Validate that a cross-plant source site is a known linked site.

    Cross-plant DM sources use ``<site>/<role>`` format. The site prefix
    must name a site in the target site's ``health/linked-sites.json``
    registry. The role part is attribution only — it belongs to the source
    site, not the target site, so it is not validated against the target
    site's registered roles.
    """
    import json
    registry_path = home / "health" / "linked-sites.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        sites = registry["sites"] if isinstance(registry, dict) and registry.get("version") == 1 else None
        if not isinstance(sites, list):
            raise ValueError("invalid linked-site registry")
        for site in sites:
            if isinstance(site, dict) and site.get("session") == site_name:
                return
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise ValueError(f"site '{site_name}' is not a known linked site for {home}")


def cmd_wall(args) -> int:
    from . import wall
    if args.wall_command == "show":
        print(wall.context(args.home, args.owner))
    elif args.wall_command == "write":
        _require_site_role(args.home, args.owner)
        source = _staging_notes(args.home, args.owner, args.file)
        text = source.read_text(encoding="utf-8")
        wall.reject_stale_staging_anchor(
            args.home, args.owner, text, source,
            _staging_anchor(args.home, args.owner, args.file) is not None)
        wall.write(args.home, args.owner, text, source_path=str(source))
    elif args.wall_command == "dm":
        if "/" in args.source:
            _require_linked_site(args.home, args.source.split("/", 1)[0])
        elif args.source != "operator":          # the human owner is not a role
            _require_site_role(args.home, args.source)
        if args.file:
            anchor = _staging_anchor(args.home, args.source, args.file)
            args.file = _staging_notes(args.home, args.source, args.file)
        else:
            anchor = None
        anchor_stat = anchor.stat() if anchor is not None else None
        text = args.file.read_text(encoding="utf-8") if args.file else args.text
        print(wall.message(args.home, args.source, args.to, text).sequence)
        # Consume the staging anchor after successful send so a second send requires
        # re-staging. Verify inode+mtime before unlink: if the anchor was restaged
        # mid-flight, keep the newer bytes and warn instead of destroying them.
        if anchor is not None:
            try:
                current = anchor.stat()
            except FileNotFoundError:
                pass  # already consumed externally
            else:
                if current.st_ino == anchor_stat.st_ino and current.st_mtime == anchor_stat.st_mtime:
                    anchor.unlink()
                else:
                    print(f"warning: staging anchor {anchor} changed during send; not consumed")
    elif args.wall_command == "outcome":
        _require_site_role(args.home, args.owner)
        source = _staging_notes(args.home, args.owner, args.file)
        print(wall.outcome(args.home, args.owner, args.kind, source.read_text(encoding="utf-8"),
                           args.evidence, args.supersedes).sequence)
    return 0


def cmd_access(args) -> int:
    if args.access_command == "recover":
        row = access.recover(args.home, args.id, owner=args.owner, task=args.task,
                             resolver=args.resolver, missing=args.missing, action=args.action,
                             alternatives=args.alternative, cutoff=args.cutoff, evidence=args.evidence,
                             capability=args.capability, unblocks=args.unblocks, reason=args.reason)
        print(f"{row['id']} {row['status']}; next: {row['action']}; continue other useful work")
        return 0
    if args.access_command == "resolve":
        access.resolve_recovery(args.home, args.id, args.evidence,
                                checked_action=args.checked_action, via_alternative=args.via_alternative)
        print(f"{args.id} resolved with evidence")
        return 0
    if args.access_command == "recoveries":
        for row in access.recoveries(args.home):
            print(f"{row['id']} {row['status']} owner={row['owner']} resolver={row['resolver']} "
                  f"next={row['action']} alternatives={'; '.join(row['alternatives'])} cutoff={row['cutoff']}")
        return 0
    if args.access_command is None or (args.access_command in {"grant", "revoke"} and args.id is None):
        raise ValueError("access needs a subcommand and, for grant/revoke, a request id")
    if args.access_command == "request":
        item = access.request(args.home, args.id, args.owner, args.task, args.capability,
                              args.unblocks, args.reason)
        print(f"request {item.identity} pending; unblocks={','.join(item.unblocks)}")
        return 0
    if args.access_command in {"grant", "revoke"}:
        decision = access.decide(args.home, args.id,
                                 "granted" if args.access_command == "grant" else "revoked")
        print(f"{args.id} {decision}")
        return 0
    if args.access_command == "check":
        decision = access.state(args.home, args.id)
        print(f"{args.id} {decision}")
        return 0 if decision == "granted" else 1
    for item, decision in access.list_requests(args.home):
        print(f"{item.identity} {decision} owner={item.owner} task={item.task} "
              f"capability={item.capability} unblocks={','.join(item.unblocks)}")
    return 0


def cmd_discover(args) -> int:
    if args.discover_command == "scan":
        print(discovery.scan(args.home))
        return 0
    snapshot = discovery.latest(args.home)
    if snapshot is None:
        print("UNKNOWN discovery has no scan")
        return 1
    from .scan_freshness import age_bounds, classify_age
    freshness = classify_age(age_bounds(snapshot), discovery.RENEWAL_MAX_AGE_SECONDS)
    print(f"scan {snapshot.get('created', 'unknown')} node={snapshot.get('node', 'unknown')} "
          f"acquisition={freshness}")
    for item in snapshot["observations"]:
        state = item['state'] if freshness == 'recent' else f"UNKNOWN retained={item['state']}"
        print(f"{item['id']} {state} sample={item['sample']}")
    return 0


def cmd_feed(args) -> int:
    data = Feed(args.home).read_bytes()
    parse_feed(data)
    sys.stdout.buffer.write(data)
    return 0


def cmd_dispatch_receipt(args) -> int:
    slug = validate_slug(args.slug)
    if args.entry < 1:
        raise ValueError("entry must be positive")
    feed = Feed(args.home)
    entry = feed.record_dispatch_receipt(slug, args.entry, args.outcome, request_id=args.request_id, generation=args.generation)
    print(entry.sequence)
    return 0


def cmd_pain_list(args) -> int:
    for slug in discover(args.home):
        print(slug)
    return 0


def cmd_pain_render(args) -> int:
    slug = validate_slug(args.slug)
    from .roles import declared
    if slug in declared(args.home):
        # One renderer for a mind's pane: the same plant_view the live panes run (model, wake, yield, commits, wall, signals, next due,
        # mail). The old launcher chain (top-pains/* -> render_admission -> wall_view) was deleted with the pane runtime and printed
        # only a renderer failure plus stale blocks in its place.
        import time
        from . import plant_view
        sys.stdout.write(plant_view.render(args.home, slug, now=time.time()).rstrip("\n") + "\n")
        return 0
    rendered = compose_frame(args.home, slug, args.timeout)
    sys.stdout.write(rendered.body)
    return 0 if rendered.ok else 1


def cmd_pain_read(args) -> int:
    validate_slug(args.slug)
    launcher = args.launcher
    if launcher == "dashboard":
        # No resident renderer publishes frames any more: render the current report now.
        return cmd_pain_render(args)
    return cmd_pain_render(args)











def cmd_check(args) -> int:
    argv = list(args.program)
    if argv and argv[0] == "--":
        argv.pop(0)
    try:
        path = run_check(args.home, args.slug, argv)
    except HealthPublishBusy as exc:
        print(f"BUSY {exc}", file=sys.stderr)
        return 75
    print(path)
    return 0


def cmd_predict(args) -> int:
    body = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    entry = append_prediction(Feed(args.home), validate_slug(args.slug), body, replaces=args.replaces)
    print(entry.sequence)
    return 0














def cmd_publication(args) -> int:
    from .post_check import review
    if args.publication_command == "status":
        configured = (args.home / "publication-check.json").is_file()
        print("Publication semantics: " + ("configured; every verdict must pass" if configured else "UNTESTED; only deterministic admission active"))
        paths = list((args.home / "post-checks").glob("*.json"))
        if paths:
            path = max(paths, key=lambda p: p.stat().st_mtime_ns)
            report = json.loads(path.read_text())
            print(f"Latest private check: {report.get('status', 'pending')} stage={report['stage']} source={report['source']}; report: {path}")
        return 0 if configured else 1
    from .coordination_checks import episode
    body = args.file.read_text(encoding="utf-8")
    context = episode(args.home, args.source, body, identity=args.task)
    report = review(args.home, args.source, body, context=context, stage=args.stage)
    print(f"Publication check: {report['status']}; private report: {report['report_path']}")
    for row in report['results']:
        if row['verdict'] != "clear":
            print(f"{row['id']}: {row['verdict']} — {row['reason']}")
    if report.get('error'):
        print(report['error'])
    return 0 if report['clear'] else 2


def cmd_task(args) -> int:
    from . import task_state

    if args.task_command == "coordination-report":
        from .coordination_checks import anomalies
        entries = Feed(args.home).entries()
        if args.cutoff is not None:
            entries = [entry for entry in entries if entry.sequence <= args.cutoff]
        findings = [f for f in anomalies(entries) if not args.task or f["task"] == args.task]
        print(f"Coordination history: {len(entries)} entries; {len(findings)} findings.")
        for item in findings:
            print(f"{item['severity']} {item['task']} {item['kind']}: {item['message']} Evidence sequences: {item['sequences']}")
        return int(any(f["severity"] in {"RED", "UNKNOWN"} for f in findings))
    if args.task_command in {"landing-status", "production-check"}:
        from .landing import line
        from .delivery import line as deliveries
        entries = Feed(args.home).entries()
        print(deliveries(args.home))
        print(line(entries))
        return 0
    if args.task_command not in {None, "show"}:
        raise ValueError("task-ledger mutation is retired; use walls and addressed messages")
    entries = Feed(args.home).entries()
    print("\n".join(task_state.lines(entries, args.owner, args.home) if args.owner else task_state.board(entries, args.home)) or "No historical open tasks.")
    return 0


def cmd_delivery(args) -> int:
    from . import delivery

    print(delivery.line(args.home))
    return 0





def _repair_handoffs(home: Path, entries) -> list[str]:
    latest: dict[str, str] = {}
    for entry in entries:
        if entry.source != "mishe-tauftauf" or not entry.body.startswith("handoff top-pain "):
            continue
        first, _, text = entry.body.partition("\n")
        parts = first.split()
        if len(parts) >= 4:
            latest[parts[2]] = text
    repaired = []
    for slug, text in latest.items():
        path = home / "handoffs" / f"{slug}.md"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
            repaired.append(slug)
    return repaired






def _live_check(label: str, call, failures_box: list, verbose: bool) -> bool:
    """Run the paired smoke controls for one System One adapter.

    A control that does not separate is a visible failure, never a weakened
    threshold: mishe-tauftauf never reports an unexercised judge as healthy.
    """
    started = time.monotonic()
    for question, pair in controls().items():
        for expected, evidence in (("yes", pair[0]), ("no", pair[1])):
            request = document(question, "control", "CONTROL TOP PAIN", evidence)
            try:
                probability, reason = call(request)
            except Exception as exc:
                probability, reason = None, str(exc)
            outcome = classify(question, probability)
            if verbose:
                preview = evidence if len(evidence) <= 120 else evidence[:117] + "..."
                print(
                    f"CONTROL {question} expected={expected} "
                    f"probability={probability if probability is not None else 'unknown'} "
                    f"outcome={outcome} input={preview}"
                )
            if outcome != expected:
                failures_box.append((question, expected, outcome))
    huge_request = document("publish", "control", "CONTROL", "x " * 1_000_000)
    try:
        probability, reason = call(huge_request)
    except Exception as exc:
        probability, reason = None, str(exc)
    outcome = classify("publish", probability)
    if verbose:
        print(f"OVERSIZED outcome={outcome} reason={reason}")
    if outcome != "unknown":
        failures_box.append(("oversized", "unknown", outcome))
    if verbose:
        print(f"MODEL {label} elapsed={time.monotonic()-started:.3f}s")
    return False


def _laya_call(request: str) -> tuple[float | None, str]:
    from .laya_judge import judge as laya_judge

    return laya_judge(request)


def _jev_call(request: str) -> tuple[float | None, str]:
    """Invoke the optional hosted Jev adapter through its own text protocol."""
    import subprocess as _subprocess
    import sys as _sys

    result = _subprocess.run(
        [_sys.executable or "python3", "-m", "mishe_tauftauf.jev_judge"],
        input=request.encode("utf-8"),
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        return None, f"adapter exit {result.returncode}"
    output = result.stdout.decode("utf-8", "replace").rstrip("\n")
    if output.startswith("unknown "):
        return None, output[len("unknown "):]
    if not output.startswith("probability "):
        return None, "adapter returned no result line"
    try:
        value = float(output[len("probability "):])
    except ValueError:
        return None, "adapter returned a non-numeric probability"
    if value != value or not 0.0 <= value <= 1.0:
        return None, f"adapter probability outside [0,1]: {value}"
    return value, ""

def _prog_name() -> str:
    """Report the name the launcher was actually invoked as in help output."""
    name = Path(sys.argv[0]).name
    return name if name in ("mishe", "mishe-tauftauf") else "mishe-tauftauf"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog=_prog_name())
    root.add_argument("--home", type=Path, default=default_home())
    sub = root.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init"); p.set_defaults(func=cmd_init)
    p = sub.add_parser("append"); p.add_argument("--source", required=True); p.add_argument("text", nargs="?"); p.set_defaults(func=cmd_append)
    wall = sub.add_parser("wall").add_subparsers(dest="wall_command", required=True)
    p = wall.add_parser("show"); p.add_argument("--owner", required=True); p.set_defaults(func=cmd_wall)
    p = wall.add_parser("write"); p.add_argument("--owner", required=True); p.add_argument("--file", type=Path, required=True); p.set_defaults(func=cmd_wall)
    p = wall.add_parser("outcome"); p.add_argument("--owner", required=True)
    p.add_argument("--kind", choices=("accepted", "blocker-resolved", "blocker-retired", "hypothesis-changed"), required=True)
    p.add_argument("--file", type=Path, required=True); p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--supersedes", action="append", default=None,
                   help="record digest of an earlier outcome this one re-evidences (repeatable)")
    p.set_defaults(func=cmd_wall)
    p = wall.add_parser("dm"); p.add_argument("--source", required=True); p.add_argument("--to", required=True)
    message = p.add_mutually_exclusive_group(required=True)
    message.add_argument("--file", type=Path); message.add_argument("--text"); p.set_defaults(func=cmd_wall)
    delivery = sub.add_parser("delivery").add_subparsers(dest="delivery_command", required=True)
    p = delivery.add_parser("show"); p.set_defaults(func=cmd_delivery)
    task = sub.add_parser("task").add_subparsers(dest="task_command", required=True)
    p = task.add_parser("landing"); p.add_argument("id"); p.add_argument("--source", choices=("genome", "operator"), required=True)
    p.add_argument("--producer", required=True); p.add_argument("--reason", required=True)
    p.add_argument("--evidence", type=Path, required=True); p.set_defaults(func=cmd_task)
    for action in ("landing-status", "production-check"):
        p = task.add_parser(action); p.set_defaults(func=cmd_task)
    for action in ("step", "wait"):
        p = task.add_parser(action); p.add_argument("id"); p.add_argument("--owner", required=True)
        p.add_argument("--next-step", required=True); p.add_argument("--evidence", type=Path, required=True)
        if action == "step":
            p.add_argument("--progress", required=True)
        else:
            p.add_argument("--reason", required=True); p.add_argument("--retry-event"); p.add_argument("--retry-at")
            p.add_argument("--retry-task"); p.add_argument("--producer"); p.add_argument("--alternative")
        p.set_defaults(func=cmd_task)
    p = task.add_parser("event"); p.add_argument("event"); p.add_argument("--source", required=True)
    p.add_argument("--reason", required=True); p.add_argument("--evidence", type=Path, required=True); p.set_defaults(func=cmd_task)
    p = task.add_parser("show"); p.add_argument("--owner"); p.set_defaults(func=cmd_task)
    p = task.add_parser("claim"); p.add_argument("id"); p.add_argument("--owner", required=True)
    p.add_argument("--wake", type=int, required=True); p.add_argument("--reason", required=True)
    p.add_argument("--evidence", type=Path, required=True); p.set_defaults(func=cmd_task)
    p = task.add_parser("offer"); p.add_argument("id"); p.add_argument("--owner", required=True)
    p.add_argument("--helper", action="append", default=[]); p.add_argument("--evidence", type=Path, required=True)
    p.set_defaults(func=cmd_task)
    for action in ("add", "reopen", "finish"):
        p = task.add_parser(action); p.add_argument("id"); p.add_argument("--owner", required=True)
        p.add_argument("--evidence", type=Path, required=True)
        if action == "finish":
            p.add_argument("--result", required=True)
        else:
            p.add_argument("--next-step", required=True); p.add_argument("--reason", required=True)
            if action == "add":
                p.add_argument("--parent")
        p.set_defaults(func=cmd_task)
    access_parser = sub.add_parser("access", aliases=["permit"])
    access_parser.set_defaults(func=cmd_access)
    access_cmd = access_parser.add_subparsers(dest="access_command")
    p = access_cmd.add_parser("request"); p.add_argument("id"); p.add_argument("--owner", required=True); p.add_argument("--task", required=True); p.add_argument("--capability", required=True); p.add_argument("--unblocks", action="append", required=True); p.add_argument("--reason", required=True); p.set_defaults(func=cmd_access)
    p = access_cmd.add_parser("recover")
    p.add_argument("id")
    for name in ("owner", "task", "resolver", "missing", "action", "cutoff"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--alternative", action="append", required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--capability")
    p.add_argument("--unblocks", action="append")
    p.add_argument("--reason")
    p.set_defaults(func=cmd_access)
    p = access_cmd.add_parser("resolve")
    p.add_argument("id")
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--checked-action", required=True)
    p.add_argument("--via-alternative", action="store_true")
    p.set_defaults(func=cmd_access)
    p = access_cmd.add_parser("recoveries"); p.set_defaults(func=cmd_access)
    for verb in ("grant", "revoke", "check"):
        p = access_cmd.add_parser(verb); p.add_argument("id", nargs="?" if verb != "check" else None); p.set_defaults(func=cmd_access)
    p = access_cmd.add_parser("list"); p.set_defaults(func=cmd_access)
    discover_cmd = sub.add_parser("discover").add_subparsers(dest="discover_command", required=True)
    p = discover_cmd.add_parser("scan"); p.set_defaults(func=cmd_discover)
    p = discover_cmd.add_parser("show"); p.set_defaults(func=cmd_discover)
    p = sub.add_parser("feed"); p.set_defaults(func=cmd_feed)
    p = sub.add_parser("dispatch-receipt"); p.add_argument("slug"); p.add_argument("entry", type=int); p.add_argument("outcome", choices=("delivered", "refused")); p.add_argument("--request-id"); p.add_argument("--generation", type=int); p.set_defaults(func=cmd_dispatch_receipt)
    p = sub.add_parser("check"); p.add_argument("slug"); p.add_argument("program", nargs=argparse.REMAINDER); p.set_defaults(func=cmd_check)
    p = sub.add_parser("predict"); p.add_argument("slug"); p.add_argument("file", nargs="?"); p.add_argument("--replaces", type=int); p.set_defaults(func=cmd_predict)
    pain = sub.add_parser("pain").add_subparsers(dest="pain_command", required=True)
    p = pain.add_parser("list"); p.set_defaults(func=cmd_pain_list)
    p = pain.add_parser("render"); p.add_argument("slug"); p.add_argument("--timeout", type=float, default=None); p.set_defaults(func=cmd_pain_render)
    p = pain.add_parser("read"); p.add_argument("slug"); p.add_argument("--launcher", choices=("headless", "dashboard"), default="headless"); p.add_argument("--session"); p.add_argument("--timeout", type=float, default=None); p.set_defaults(func=cmd_pain_read)
    p = sub.add_parser("publication").add_subparsers(dest="publication_command", required=True)
    c = p.add_parser("status"); c.set_defaults(func=cmd_publication)
    c = p.add_parser("check"); c.add_argument("--source", required=True); c.add_argument("--file", type=Path, required=True); c.add_argument("--stage", choices=("post", "selection", "handoff"), default="post"); c.add_argument("--task"); c.set_defaults(func=cmd_publication)
    c = task.add_parser("coordination-report"); c.add_argument("--task"); c.add_argument("--cutoff", type=int); c.set_defaults(func=cmd_task)
    from .dispositions import register as register_dispositions
    register_dispositions(sub)
    from .guard_cli import register as register_guard
    register_guard(sub)
    from .engine_usage import register as register_engine
    register_engine(sub)
    from .runtime_cli import register as register_runtime
    register_runtime(sub)
    from .token_usage import register as register_stats
    register_stats(sub)
    return root


def main(argv: list[str] | None = None) -> int:
    from .runtime_cli import cmd_runtime, command_tail
    argv, tail = command_tail(list(sys.argv[1:] if argv is None else argv))
    args = parser().parse_args(argv)
    if tail:
        args.cmd = tail
    args.home = args.home.resolve()
    try:
        if args.func is cmd_runtime:        # runtime commands run on any site, including a scratch one
            return args.func(args)
        validate_home_binding(args.home)
        if args.func is not cmd_init:
            validate_home(args.home)
        return args.func(args)
    except (FeedError, PredictionError, ValueError, RuntimeError) as exc:
        print(f"mishe-tauftauf: {exc}", file=sys.stderr)
        from .post_check import CorrectionRequired
        if isinstance(exc, CorrectionRequired):
            for row in exc.report["results"]:
                if row["verdict"] != "clear":
                    print(f"{row['id']}: {row['verdict']} — {row['reason']}", file=sys.stderr)
            if exc.report.get("error"):
                print(exc.report["error"], file=sys.stderr)
            print(exc.report["required_correction"], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
