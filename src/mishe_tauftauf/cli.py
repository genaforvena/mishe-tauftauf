from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .checks import run_check
from . import access, discovery
from .feed import Feed, FeedError, parse_feed, utc_now
from .judges import QUESTIONS, classify, controls, document, run_external
from .observations import compose_frame, discover, executable, validate_home, validate_slug
from .predictions import PredictionError, append_prediction, pending_predictions, replay_predictions
from .runtime import Coordinator, RuntimeConfig, runtime_status


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
    home.mkdir(parents=True, exist_ok=True)
    for name in ("top-pains", "minds", "observations", "filters", "projectors", "handoffs", "plans"):
        (home / name).mkdir(exist_ok=True)
    if not (home / "feed").exists():
        (home / "chat.log").touch(mode=0o600, exist_ok=True)


def cmd_init(args) -> int:
    from .seed import _require_worktree
    _require_worktree(args.home)
    initialize(args.home)
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
    print(entry.sequence)
    return 0


def cmd_access(args) -> int:
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
    print(f"scan {snapshot['created']} node={snapshot['node']}")
    for item in snapshot["observations"]:
        print(f"{item['id']} {item['state']} sample={item['sample']}")
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
    rendered = compose_frame(args.home, validate_slug(args.slug), args.timeout)
    sys.stdout.write(rendered.body)
    return 0 if rendered.ok else 1


def cmd_pain_read(args) -> int:
    validate_slug(args.slug)
    launcher = args.launcher
    if launcher == "dashboard":
        from .dashboard import read
        try:
            frame, ok = read(args.home, args.slug)
        except ValueError as exc:
            sys.stdout.write(f"UNKNOWN — {exc}\n")
            return 1
        sys.stdout.write(frame)
        return 0 if ok else 1
    if launcher == "tmux":
        from .tmux import capture_raw, owns_session
        # A typo'd or stale --home must not read another site's live pane as if
        # it were this one: the pane is addressed only by session and window,
        # so without this check every pane reads GREEN under a wrong home.
        if not owns_session(args.home, args.session):
            home = Path(args.home).resolve()
            sys.stderr.write(f"UNKNOWN — session {args.session!r} is not owned by {home}\n")
            return 1
        text = capture_raw(args.session, args.slug)
        sys.stdout.write(text)
        return 1 if text.startswith("UNKNOWN —") else 0
    return cmd_pain_render(args)


def cmd_pain_watch(args) -> int:
    validate_slug(args.slug)
    feed = Feed(args.home)
    try:
        while True:
            ok = False
            try:
                rendered = compose_frame(args.home, args.slug, args.timeout)
                ok = rendered.ok
                entries = feed.entries()
                status = runtime_status(entries, args.slug)
                frame = rendered.body
                if not frame.endswith("\n"):
                    frame += "\n"
                frame += f"-- runtime: {status} --\n"
            except FeedError as exc:
                ok = False
                frame = (
                    "STATE: RED — chat feed is malformed; repair the framed feed before retrying\n"
                    f"FEED ERROR: {exc}\n"
                )
            frame += f"-- pane live {utc_now()} · refresh {args.interval:g}s · ticks every frame --\n"
            from .dashboard import publish
            publish(args.home, args.slug, frame, ok)
            sys.stdout.write("\x1b[H\x1b[2J" + frame)
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_check(args) -> int:
    argv = list(args.program)
    if argv and argv[0] == "--":
        argv.pop(0)
    path = run_check(args.home, args.slug, argv)
    print(path)
    return 0


def cmd_predict(args) -> int:
    body = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    entry = append_prediction(Feed(args.home), validate_slug(args.slug), body, replaces=args.replaces)
    print(entry.sequence)
    return 0


def cmd_handoff(args) -> int:
    slug = validate_slug(args.slug)
    invocation = os.environ.get("MISHE_TAUFTAUF_INVOCATION")
    env_slug = os.environ.get("MISHE_TAUFTAUF_SLUG")
    if not invocation or env_slug != slug:
        raise ValueError("handoff requires launcher-supplied matching invocation identity")
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    if not text:
        raise ValueError("handoff text must be non-empty")
    text.encode("utf-8")
    entry = Feed(args.home).append_runtime("mishe-tauftauf", f"handoff top-pain {slug} invocation {invocation}\n{text}")
    directory = args.home / "handoffs"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{slug}.{os.getpid()}.tmp"
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, directory / f"{slug}.md")
    print(entry.sequence)
    return 0


def cmd_run(args) -> int:
    config = RuntimeConfig(args.home, Path(args.judge) if args.judge else None, args.launcher, args.session, args.interval, not args.observe_only, Path(args.policy) if args.policy else None, Path(args.batch_judge) if args.batch_judge else None, args.control_cache_ttl, args.refresh_controls, tuple(args.external_view_slug), tuple(args.external_delta_view_slug), slug=args.slug, laya_structured=args.laya_structured, external_fleet_view_slugs=tuple(args.external_fleet_view_slug))
    coordinator = Coordinator(config)
    coordinator.acquire()
    try:
        if args.follow:
            coordinator.follow()
        else:
            coordinator.pass_once()
    finally:
        coordinator.close()
    return 0


def cmd_tmux_start(args) -> int:
    from .tmux import start
    start(args.home, args.session, args.interval)
    print(f"tmux session {args.session} ready")
    return 0


def cmd_tmux_stop(args) -> int:
    from .tmux import stop
    stop(args.home, args.session)
    print(f"tmux session {args.session} stopped")
    return 0


def cmd_seed(args) -> int:
    from . import seed

    action = args.seed_command
    if action == "init":
        result = seed.init(args.home, args.slug, args.engine_command)
    elif action == "start":
        result = seed.start(args.home, args.session, args.slug, args.interval)
    elif action == "stop":
        result = seed.stop(args.home, args.session)
    elif action == "tick":
        result = seed.tick(args.home, args.session, args.slug, args.self_pick_seconds)
    elif action == "follow":
        seed.follow(args.home, args.session, args.slug, args.interval, args.self_pick_seconds)
        return 0
    elif action == "run":
        seed.run(args.home, args.session, args.slug, args.interval, args.self_pick_seconds, args.clear_grace_seconds)
        return 0
    elif action == "yield":
        result = seed.yield_wake(args.home, args.slug, args.wake, args.file, args.continue_task, args.result)
    elif action == "clear":
        result = seed.clear(args.home, args.session, args.slug)
    else:
        result = seed.status(args.home, args.slug)
    print(result)
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
            print(f"Latest private check: {report['status']} stage={report['stage']} source={report['source']}; report: {path}")
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
    if args.task_command == "landing":
        from .landing import register
        print(register(args.home, args.id, args.source, args.producer, args.reason, args.evidence).sequence)
        return 0
    if args.task_command == "step":
        entry = task_state.set_step(args.home, args.id, args.owner, args.next_step, args.progress, args.evidence)
    elif args.task_command == "claim":
        entry = task_state.claim(args.home, args.id, args.owner, args.wake, args.reason, args.evidence)
    elif args.task_command == "wait":
        entry = task_state.wait_for(args.home, args.id, args.owner, args.next_step, args.reason, args.evidence,
                                    retry_event=args.retry_event, retry_at=args.retry_at,
                                    retry_task=args.retry_task, producer=args.producer, alternative=args.alternative)
    elif args.task_command == "add":
        entry = task_state.add_task(args.home, args.id, args.owner, args.next_step, args.reason, args.evidence,
                                    parent=args.parent)
    elif args.task_command == "reopen":
        entry = task_state.reopen(args.home, args.id, args.owner, args.next_step, args.reason, args.evidence)
    elif args.task_command == "finish":
        entry = task_state.finish(args.home, args.id, args.owner, args.result, args.evidence)
    elif args.task_command == "event":
        entry = task_state.signal(args.home, args.event, args.source, args.evidence, args.reason)
    elif args.task_command == "offer":
        entry = task_state.offer(args.home, args.id, args.owner, args.helper, args.evidence)
    else:
        entries = Feed(args.home).entries()
        print("\n".join(task_state.lines(entries, args.owner) if args.owner else task_state.board(entries)) or "No open tasks.")
        return 0
    print(entry.sequence)
    return 0


def cmd_delivery(args) -> int:
    import json
    from . import delivery

    action = args.delivery_command
    if action == "submit":
        result = delivery.submit(args.home, args.id, args.owner, args.repo, args.base, args.branch, args.review)
    elif action == "check":
        result = delivery.check(args.home, args.id)
    elif action == "integrate":
        result = delivery.integrate(args.home, args.id, args.source)
    elif action == "retire":
        from .retirement import retire
        result = retire(args.home, args.id)
    elif action == "finish":
        result = delivery.finish(args.home, args.id, args.owner, args.evidence)
    else:
        print(delivery.line(args.home))
        return 0
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_tmux_mind_run(args) -> int:
    from .feed import Feed
    from .tmux import capture_raw
    home, slug = args.home, validate_slug(args.slug)
    validate_home(home)
    lock_path = home / "minds" / f".{slug}.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        context_path = Path(args.context_path)
        context = context_path.read_text(encoding="utf-8")
        context_path.unlink(missing_ok=True)
        pane = capture_raw(args.session, slug)
        context = f"ACTUAL LAUNCH-TIME TOP PAIN\n{pane}\n\n{context}"
        executable_path = home / "minds" / slug
        if not executable(executable_path):
            executable_path = home / "minds" / "default"
        env = os.environ.copy()
        env.update({"MISHE_TAUFTAUF_HOME": str(home), "MISHE_TAUFTAUF_SLUG": slug, "MISHE_TAUFTAUF_INVOCATION": args.invocation, "MISHE_TAUFTAUF_WORKSPACE": str(home.parent)})
        try:
            process = subprocess.Popen([str(executable_path)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(context.encode())
            process.stdin.close()
            byte_count = 0
            while chunk := os.read(process.stdout.fileno(), 4096):
                byte_count += len(chunk)
                remaining = memoryview(chunk)
                while remaining:
                    remaining = remaining[os.write(1, remaining):]
            code = process.wait()
            stdout, stderr = "", ""
        except OSError as exc:
            code, stdout, stderr = 127, "", str(exc)
        feed = Feed(home)
        if stdout or stderr:
            print(stdout + stderr, flush=True)
        if 'byte_count' in locals():
            feed.append_runtime("mishe-tauftauf", f"mind output top-pain {slug} invocation {args.invocation} stdout-bytes={byte_count} stderr-bytes=0")
        feed.append_runtime("mishe-tauftauf", f"mind exited top-pain {slug} for entry {args.sequence} attempt={args.attempt} code={code}")
        if not any(f"handoff top-pain {slug} invocation {args.invocation}" in entry.body for entry in feed.entries() if entry.source == "mishe-tauftauf"):
            feed.append_runtime("observation/" + slug, f"UNKNOWN — mind invocation {args.invocation} exited without a tied handoff; prior handoff is stale")
        return code


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


def cmd_doctor(args) -> int:
    failures = 0
    site = args.home.resolve()
    repository = subprocess.run(["git", "-C", str(site.parent), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True)
    if repository.returncode == 0 and Path(repository.stdout.strip()).resolve() == site.parent:
        tracked = subprocess.run(["git", "-C", str(site.parent), "ls-files", "--", site.name],
                                 capture_output=True, text=True)
        if tracked.returncode or tracked.stdout.strip():
            print("HOLD local-plant-in-git: remove staged or tracked site files from the index")
            failures += 1
        else:
            print("PASS local plant out of Git")
    feed = Feed(args.home)
    try:
        entries = feed.entries()
        print(f"PASS feed: {len(entries)} contiguous entries")
        print(f"INFO feed bytes: {len(feed.read_bytes())}")
    except FeedError as exc:
        print(f"HOLD feed-corrupt: {exc}")
        return 1
    for kind in ("top-pains", "minds"):
        directory = args.home / kind
        if not directory.exists():
            print(f"HOLD missing-directory: {directory}")
            failures += 1
            continue
        bad = [path.name for path in directory.iterdir() if path.is_file() and not executable(path) and not path.name.startswith(".")]
        if bad:
            print(f"HOLD non-executable {kind}: {', '.join(sorted(bad))}")
            failures += 1
        if kind == "top-pains" and not discover(args.home):
            print("HOLD missing-top-pain: no executable Top Pain in top-pains")
            failures += 1
        elif not bad:
            print(f"PASS executable {kind}")
    repaired = _repair_handoffs(args.home, entries)
    if repaired:
        print("PASS repaired handoff projections: " + ", ".join(repaired))
    pending = pending_predictions(args.home, entries)
    for prediction in pending:
        print(f"PENDING prediction {prediction.sequence} top-pain {prediction.slug} check {prediction.check_at.isoformat()}")
    if args.panes:
        from .tmux import check_pane
        for slug in discover(args.home):
            ok, line = check_pane(args.home, args.session, slug, args.pane_wait)
            print(line)
            failures += 0 if ok else 1
    if shutil.which("tmux"):
        print("PASS optional tmux available")
    else:
        print("UNAVAILABLE optional tmux")
    try:
        import laya  # noqa: F401
        print("PASS optional laya available")
    except ImportError:
        print("UNAVAILABLE optional laya")
    if args.live_laya:
        box: list = []
        _live_check("convaiinnovations/laya typed-decisions", _laya_call, box, args.verbose)
        failures += len(box)
    if args.live_jev:
        box2: list = []
        _live_check("typesafe/jev", _jev_call, box2, args.verbose)
        failures += len(box2)
    return 1 if failures else 0


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


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="mishe-tauftauf")
    root.add_argument("--home", type=Path, default=default_home())
    sub = root.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init"); p.set_defaults(func=cmd_init)
    p = sub.add_parser("append"); p.add_argument("--source", required=True); p.add_argument("text", nargs="?"); p.set_defaults(func=cmd_append)
    delivery = sub.add_parser("delivery").add_subparsers(dest="delivery_command", required=True)
    p = delivery.add_parser("submit"); p.add_argument("id"); p.add_argument("--owner", required=True)
    p.add_argument("--repo", type=Path, required=True); p.add_argument("--base", required=True)
    p.add_argument("--branch", required=True); p.add_argument("--review", type=Path, required=True)
    p.set_defaults(func=cmd_delivery)
    p = delivery.add_parser("check"); p.add_argument("id"); p.set_defaults(func=cmd_delivery)
    p = delivery.add_parser("show"); p.set_defaults(func=cmd_delivery)
    p = delivery.add_parser("retire"); p.add_argument("id"); p.set_defaults(func=cmd_delivery)
    p = delivery.add_parser("integrate"); p.add_argument("id")
    p.add_argument("--source", choices=("genome", "operator"), required=True); p.set_defaults(func=cmd_delivery)
    p = delivery.add_parser("finish"); p.add_argument("id"); p.add_argument("--owner", required=True)
    p.add_argument("--evidence", type=Path, required=True); p.set_defaults(func=cmd_delivery)
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
    access_cmd = sub.add_parser("access").add_subparsers(dest="access_command", required=True)
    p = access_cmd.add_parser("request"); p.add_argument("id"); p.add_argument("--owner", required=True); p.add_argument("--task", required=True); p.add_argument("--capability", required=True); p.add_argument("--unblocks", action="append", required=True); p.add_argument("--reason", required=True); p.set_defaults(func=cmd_access)
    for verb in ("grant", "revoke", "check"):
        p = access_cmd.add_parser(verb); p.add_argument("id"); p.set_defaults(func=cmd_access)
    p = access_cmd.add_parser("list"); p.set_defaults(func=cmd_access)
    discover_cmd = sub.add_parser("discover").add_subparsers(dest="discover_command", required=True)
    p = discover_cmd.add_parser("scan"); p.set_defaults(func=cmd_discover)
    p = discover_cmd.add_parser("show"); p.set_defaults(func=cmd_discover)
    p = sub.add_parser("feed"); p.set_defaults(func=cmd_feed)
    p = sub.add_parser("dispatch-receipt"); p.add_argument("slug"); p.add_argument("entry", type=int); p.add_argument("outcome", choices=("delivered", "refused")); p.add_argument("--request-id"); p.add_argument("--generation", type=int); p.set_defaults(func=cmd_dispatch_receipt)
    p = sub.add_parser("doctor"); p.add_argument("--panes", action="store_true"); p.add_argument("--session", default="mishe-tauftauf"); p.add_argument("--pane-wait", type=float, default=11.0); p.add_argument("--live-laya", action="store_true"); p.add_argument("--live-jev", action="store_true"); p.add_argument("--verbose", action="store_true"); p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("check"); p.add_argument("slug"); p.add_argument("program", nargs=argparse.REMAINDER); p.set_defaults(func=cmd_check)
    p = sub.add_parser("predict"); p.add_argument("slug"); p.add_argument("file", nargs="?"); p.add_argument("--replaces", type=int); p.set_defaults(func=cmd_predict)
    p = sub.add_parser("handoff"); p.add_argument("slug"); p.add_argument("file", nargs="?"); p.set_defaults(func=cmd_handoff)
    pain = sub.add_parser("pain").add_subparsers(dest="pain_command", required=True)
    p = pain.add_parser("list"); p.set_defaults(func=cmd_pain_list)
    p = pain.add_parser("render"); p.add_argument("slug"); p.add_argument("--timeout", type=float, default=10.0); p.set_defaults(func=cmd_pain_render)
    p = pain.add_parser("read"); p.add_argument("slug"); p.add_argument("--launcher", choices=("headless", "tmux", "dashboard"), default="headless"); p.add_argument("--session", default="mishe-tauftauf"); p.add_argument("--timeout", type=float, default=10.0); p.set_defaults(func=cmd_pain_read)
    p = pain.add_parser("watch"); p.add_argument("slug"); p.add_argument("--interval", type=float, default=5.0); p.add_argument("--timeout", type=float, default=10.0); p.set_defaults(func=cmd_pain_watch)
    p = sub.add_parser("run"); mode = p.add_mutually_exclusive_group(required=True); mode.add_argument("--once", action="store_true"); mode.add_argument("--follow", action="store_true"); judge = p.add_mutually_exclusive_group(); judge.add_argument("--judge"); judge.add_argument("--batch-judge"); p.add_argument("--launcher", choices=("headless", "tmux"), default="tmux"); p.add_argument("--session", default="mishe-tauftauf"); p.add_argument("--interval", type=float, default=5.0); p.add_argument("--observe-only", action="store_true"); p.add_argument("--slug"); p.add_argument("--laya-structured", action="store_true"); p.add_argument("--policy"); p.add_argument("--control-cache-ttl", type=float); p.add_argument("--refresh-controls", action="store_true"); p.add_argument("--external-view-slug", action="append", default=[]); p.add_argument("--external-delta-view-slug", action="append", default=[]); p.add_argument("--external-fleet-view-slug", action="append", default=[]); p.set_defaults(func=cmd_run)
    tmux = sub.add_parser("tmux").add_subparsers(dest="tmux_command", required=True)
    p = tmux.add_parser("start"); p.add_argument("--session", default="mishe-tauftauf"); p.add_argument("--interval", type=float, default=5.0); p.set_defaults(func=cmd_tmux_start)
    p = tmux.add_parser("stop"); p.add_argument("--session", default="mishe-tauftauf"); p.set_defaults(func=cmd_tmux_stop)
    seed = sub.add_parser("seed").add_subparsers(dest="seed_command", required=True)
    p = seed.add_parser("init"); p.add_argument("--slug", default="genome"); p.add_argument("--engine-command", default="codex"); p.set_defaults(func=cmd_seed)
    p = seed.add_parser("start"); p.add_argument("--slug", default="genome"); p.add_argument("--session", default="mishe-seed"); p.add_argument("--interval", type=float, default=5.0); p.set_defaults(func=cmd_seed)
    p = seed.add_parser("stop"); p.add_argument("--session", default="mishe-seed"); p.set_defaults(func=cmd_seed)
    p = seed.add_parser("tick"); p.add_argument("--slug", default="genome"); p.add_argument("--session", default="mishe-seed"); p.add_argument("--self-pick-seconds", type=float, default=0); p.set_defaults(func=cmd_seed)
    p = seed.add_parser("follow"); p.add_argument("--slug", default="genome"); p.add_argument("--session", default="mishe-seed"); p.add_argument("--interval", type=float, default=5.0); p.add_argument("--self-pick-seconds", type=float, default=3600); p.set_defaults(func=cmd_seed)
    p = seed.add_parser("run"); p.add_argument("--slug", default="genome"); p.add_argument("--session", default="mishe-seed"); p.add_argument("--interval", type=float, default=5.0); p.add_argument("--self-pick-seconds", type=float, default=3600); p.add_argument("--clear-grace-seconds", type=float, default=15); p.set_defaults(func=cmd_seed)
    p = seed.add_parser("yield"); p.add_argument("--slug", default="genome"); p.add_argument("--wake", type=int, required=True); p.add_argument("--file", type=Path, required=True); p.add_argument("--continue", dest="continue_task", action="store_true"); p.add_argument("--result", choices=("changed", "verified", "blocked"), default="unspecified"); p.set_defaults(func=cmd_seed)
    p = seed.add_parser("clear"); p.add_argument("--slug", default="genome"); p.add_argument("--session", default="mishe-seed"); p.set_defaults(func=cmd_seed)
    p = seed.add_parser("status"); p.add_argument("--slug", default="genome"); p.set_defaults(func=cmd_seed)
    p = sub.add_parser("tmux-mind-run", help=argparse.SUPPRESS); p.add_argument("slug"); p.add_argument("sequence", type=int); p.add_argument("attempt", type=int); p.add_argument("invocation"); p.add_argument("context_path"); p.add_argument("--session", default="mishe-tauftauf"); p.set_defaults(func=cmd_tmux_mind_run)
    p = sub.add_parser("publication").add_subparsers(dest="publication_command", required=True)
    c = p.add_parser("status"); c.set_defaults(func=cmd_publication)
    c = p.add_parser("check"); c.add_argument("--source", required=True); c.add_argument("--file", type=Path, required=True); c.add_argument("--stage", choices=("post", "selection", "handoff"), default="post"); c.add_argument("--task"); c.set_defaults(func=cmd_publication)
    c = task.add_parser("coordination-report"); c.add_argument("--task"); c.add_argument("--cutoff", type=int); c.set_defaults(func=cmd_task)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.home = args.home.resolve()
    try:
        if not (args.func is cmd_init or getattr(args, "seed_command", None) == "init"):
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
