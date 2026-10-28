"""The core's own command line.

This is a separate entry point from the plant's CLI.  It exists so the core
can be exercised — a dispatch, a status, a reconciliation — without adding
core commands to the plant's own command surface.

It is intentionally small:

``status``
    what the durable record says, and whether it can be trusted;
``dispatch``
    run one turn for one role through the canonical lifecycle;
``reconcile``
    close an open dispatch after checking what it actually did.

The core refuses to dispatch a role the site has not declared, and it
refuses to dispatch while an earlier dispatch is still open.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _site_home(raw: str) -> Path:
    return Path(raw).resolve()


def _supervisor(args, worker=None):
    from .adapters import CanonicalObservation, CanonicalRoleRegistry
    from .state import DispatchStore
    from .supervisor import Supervisor

    store = DispatchStore(Path(args.state) if args.state else Path(args.home) / "core-dispatch")
    registry = CanonicalRoleRegistry(args.home)
    if worker is None:
        worker = _canonical_worker(args)
    observer = CanonicalObservation(home=args.home, role=args.role)
    return Supervisor(store=store, registry=registry.registry(), worker=worker,
                      observer=observer)


def _canonical_worker(args):
    from .adapters import CanonicalWorker
    return CanonicalWorker(home=args.home, session=args.session, role=args.role,
                           timeout=getattr(args, "timeout", 900.0))


def cmd_status(args) -> int:
    from .state import DispatchStore
    store = DispatchStore(Path(args.state) if args.state else Path(args.home) / "core-dispatch")
    records = {record.role: record for record in store.records()}
    reading = _observation(args)
    print(json.dumps({
        "roles": {role: record.to_json() for role, record in records.items()},
        "open": [record.to_json() for record in store.open_records()],
        "observation": {"fresh": reading.fresh, "healthy": reading.healthy,
                        "detail": reading.detail},
    }, indent=2, sort_keys=True))
    return 0


def _observation(args):
    from .adapters import CanonicalObservation
    try:
        return CanonicalObservation(home=args.home, role=args.role)()
    except (OSError, ValueError) as exc:
        from .interfaces import Observation
        return Observation(fresh=False, healthy=False, detail=str(exc))


def cmd_dispatch(args) -> int:
    supervisor = _supervisor(args)
    record = supervisor.dispatch(args.role, args.key, payload={
        "self_pick_seconds": args.self_pick_seconds,
    })
    print(json.dumps(record.to_json(), indent=2, sort_keys=True))
    return 0 if record.state.name in {"completed", "skipped"} else 3


def cmd_reconcile(args) -> int:
    supervisor = _supervisor(args)
    record = supervisor.reconcile(args.role, detail=args.detail, key=args.key)
    print(json.dumps(record.to_json(), indent=2, sort_keys=True))
    return 0


def _attempt_key(prefix: str) -> str:
    """An attempt is claimed before tick can create its canonical wake."""
    from uuid import uuid4
    return prefix + uuid4().hex

def cmd_supervise(args) -> int:
    """Run the supervisor as a resident: one turn at a time, forever.

    The loop is the plant's own cadence, not a new one.  Each pass asks the
    canonical tick whether a turn is owed, and stops there: the tick owns
    the wake, the delivery and the refusal, and the core owns the ordering
    and the durable record.

    An open dispatch — a turn whose effect was not recorded — stops the
    loop rather than being retried.  The loop says so and waits; a human
    reconciles it with ``reconcile``.
    """
    import time
    supervisor = _supervisor(args)
    from .adapters import CanonicalLiveObservation
    live_observation = CanonicalLiveObservation(home=args.home, session=args.session,
                                                role=args.role)
    last = None
    while True:
        open_dispatch = supervisor.open_dispatch()
        if open_dispatch is not None:
            print(f"held core {args.role}: dispatch {open_dispatch.key!r} is "
                  f"{open_dispatch.state.name}; reconcile it before dispatching again",
                  flush=True)
            time.sleep(args.interval)
            continue
        receipt = live_observation()
        if not receipt.fresh:
            print(f"UNKNOWN core {args.role}: {receipt.detail}", flush=True)
            time.sleep(args.interval)
            continue
        if not receipt.healthy:
            # The plant's own sensors report a problem.  A turn driven now
            # would settle a wake while the plant says something is wrong.
            print(f"held core {args.role}: the observation reports a problem: "
                  f"{receipt.detail}", flush=True)
            time.sleep(args.interval)
            continue
        try:
            record = supervisor.dispatch(
                args.role, _attempt_key(args.key_prefix),
                payload={"self_pick_seconds": args.self_pick_seconds},
                discard_skipped_poll=True)
        except Exception as exc:
            print(f"held core {args.role}: {exc}", flush=True)
            time.sleep(args.interval)
            continue
        message = record.to_json()
        identity = (record.state.name, record.detail) if record.state.name == "skipped" else message
        if identity != last:
            print(json.dumps(message, sort_keys=True), flush=True)
            last = identity
        time.sleep(args.interval)


def parser() -> argparse.ArgumentParser:
    """The core's own argument grammar."""
    from .dispatch_record import build_parser
    return build_parser(__doc__, cmd_status, cmd_dispatch, cmd_reconcile,
                         cmd_supervise)


def main(argv: list[str] | None = None) -> int:
    from .dispatch_record import decode_arguments
    args = decode_arguments(parser(), argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"mishe-core: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
