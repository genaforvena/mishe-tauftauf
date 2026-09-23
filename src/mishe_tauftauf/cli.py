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
from .feed import Feed, FeedError, parse_feed, utc_now
from .judges import QUESTIONS, classify, controls, document, run_external
from .observations import compose_frame, discover, executable, validate_slug
from .predictions import PredictionError, append_prediction, pending_predictions, replay_predictions
from .runtime import Coordinator, RuntimeConfig, runtime_status


def default_home() -> Path:
    return Path(os.environ.get("MISHE_TAUFTAUF_HOME", ".mishe-tauftauf"))


def initialize(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    for name in ("top-pains", "minds", "observations", "filters", "projectors", "handoffs", "plans"):
        (home / name).mkdir(exist_ok=True)
    (home / "feed").touch(mode=0o600, exist_ok=True)


def cmd_init(args) -> int:
    initialize(args.home)
    print(args.home)
    return 0


def cmd_append(args) -> int:
    text = args.text if args.text is not None else sys.stdin.read()
    entry = Feed(args.home).append(args.source, text)
    print(entry.sequence)
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
    request = f"wake requested top-pain {slug} for entry {args.entry}"
    if not any(item.source == "mishe-tauftauf" and item.body == request for item in feed.entries()):
        raise ValueError("dispatch receipt requires an existing wake request")
    entry = feed.append_runtime_once("mishe-tauftauf", f"wake {args.outcome} top-pain {slug} for entry {args.entry}")
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
    if launcher == "tmux":
        from .tmux import capture_raw
        text = capture_raw(args.session, args.slug)
        sys.stdout.write(text)
        return 1 if text.startswith("UNKNOWN —") else 0
    return cmd_pain_render(args)


def cmd_pain_watch(args) -> int:
    validate_slug(args.slug)
    feed = Feed(args.home)
    try:
        while True:
            rendered = compose_frame(args.home, args.slug, args.timeout)
            entries = feed.entries()
            status = runtime_status(entries, args.slug)
            frame = rendered.body
            if not frame.endswith("\n"):
                frame += "\n"
            frame += f"-- runtime: {status} --\n"
            frame += f"-- pane live {utc_now()} · refresh {args.interval:g}s · ticks every frame --\n"
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
    config = RuntimeConfig(args.home, Path(args.judge) if args.judge else None, args.launcher, args.session, args.interval, not args.observe_only, Path(args.policy) if args.policy else None, Path(args.batch_judge) if args.batch_judge else None)
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


def cmd_tmux_mind_run(args) -> int:
    from .feed import Feed
    from .tmux import capture_raw
    home, slug = args.home, validate_slug(args.slug)
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
            result = subprocess.run([str(executable_path)], input=context.encode(), capture_output=True, env=env)
            code = result.returncode
            stdout, stderr = result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")
        except OSError as exc:
            code, stdout, stderr = 127, "", str(exc)
        feed = Feed(home)
        if stdout or stderr:
            feed.append_runtime("mishe-tauftauf", f"mind output top-pain {slug} invocation {args.invocation} stdout-bytes={len(stdout.encode())} stderr-bytes={len(stderr.encode())}")
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
        else:
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

    path = Path(__file__).resolve().parents[2] / "examples" / "jev-judge.py"
    if not path.exists():
        return None, "examples/jev-judge.py is not shipped with this install"
    result = _subprocess.run(
        [_sys.executable or "python3", str(path)],
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
    p = sub.add_parser("feed"); p.set_defaults(func=cmd_feed)
    p = sub.add_parser("dispatch-receipt"); p.add_argument("slug"); p.add_argument("entry", type=int); p.add_argument("outcome", choices=("delivered", "refused")); p.set_defaults(func=cmd_dispatch_receipt)
    p = sub.add_parser("doctor"); p.add_argument("--panes", action="store_true"); p.add_argument("--session", default="mishe-tauftauf"); p.add_argument("--pane-wait", type=float, default=11.0); p.add_argument("--live-laya", action="store_true"); p.add_argument("--live-jev", action="store_true"); p.add_argument("--verbose", action="store_true"); p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("check"); p.add_argument("slug"); p.add_argument("program", nargs=argparse.REMAINDER); p.set_defaults(func=cmd_check)
    p = sub.add_parser("predict"); p.add_argument("slug"); p.add_argument("file", nargs="?"); p.add_argument("--replaces", type=int); p.set_defaults(func=cmd_predict)
    p = sub.add_parser("handoff"); p.add_argument("slug"); p.add_argument("file", nargs="?"); p.set_defaults(func=cmd_handoff)
    pain = sub.add_parser("pain").add_subparsers(dest="pain_command", required=True)
    p = pain.add_parser("list"); p.set_defaults(func=cmd_pain_list)
    p = pain.add_parser("render"); p.add_argument("slug"); p.add_argument("--timeout", type=float, default=10.0); p.set_defaults(func=cmd_pain_render)
    p = pain.add_parser("read"); p.add_argument("slug"); p.add_argument("--launcher", choices=("headless", "tmux"), default="headless"); p.add_argument("--session", default="mishe-tauftauf"); p.add_argument("--timeout", type=float, default=10.0); p.set_defaults(func=cmd_pain_read)
    p = pain.add_parser("watch"); p.add_argument("slug"); p.add_argument("--interval", type=float, default=5.0); p.add_argument("--timeout", type=float, default=10.0); p.set_defaults(func=cmd_pain_watch)
    p = sub.add_parser("run"); mode = p.add_mutually_exclusive_group(required=True); mode.add_argument("--once", action="store_true"); mode.add_argument("--follow", action="store_true"); judge = p.add_mutually_exclusive_group(); judge.add_argument("--judge"); judge.add_argument("--batch-judge"); p.add_argument("--launcher", choices=("headless", "tmux"), default="tmux"); p.add_argument("--session", default="mishe-tauftauf"); p.add_argument("--interval", type=float, default=5.0); p.add_argument("--observe-only", action="store_true"); p.add_argument("--policy"); p.set_defaults(func=cmd_run)
    tmux = sub.add_parser("tmux").add_subparsers(dest="tmux_command", required=True)
    p = tmux.add_parser("start"); p.add_argument("--session", default="mishe-tauftauf"); p.add_argument("--interval", type=float, default=5.0); p.set_defaults(func=cmd_tmux_start)
    p = tmux.add_parser("stop"); p.add_argument("--session", default="mishe-tauftauf"); p.set_defaults(func=cmd_tmux_stop)
    p = sub.add_parser("tmux-mind-run", help=argparse.SUPPRESS); p.add_argument("slug"); p.add_argument("sequence", type=int); p.add_argument("attempt", type=int); p.add_argument("invocation"); p.add_argument("context_path"); p.add_argument("--session", default="mishe-tauftauf"); p.set_defaults(func=cmd_tmux_mind_run)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.home = args.home.resolve()
    try:
        return args.func(args)
    except (FeedError, PredictionError, ValueError, RuntimeError) as exc:
        print(f"mishe-tauftauf: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
