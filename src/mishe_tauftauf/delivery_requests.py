"""Exact delivery requests consumed after the author engine and dispatch settle.

The admission record spans subprocesses which acquire the dispatch lock
independently. It is distinct from human hold. An interrupted effect retains
admission and UNKNOWN evidence; a dead parent is never proof of recovery.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import subprocess
from pathlib import Path
import time
import uuid

from . import release_promotion as promotion, yielding, wall_patch
from .core.state import DispatchStore
from .observations import validate_slug
from .state_files import read_json, write_json

SIGNAL = "delivery-requests"


def admission_path(home):
    return Path(home) / "work/delivery-admission.json"


def location(home, identity):
    validate_slug(identity)
    return Path(home) / "work/delivery-requests" / f"{identity}.json"


def check_admission(home):
    """Only the admitted consumer and its child commands may deliver."""
    path = admission_path(home)
    if path.exists():
        marker = read_json(path)
        if not isinstance(marker, dict) or not marker.get("token") or marker["token"] != os.environ.get("MISHE_DELIVERY_TOKEN"):
            raise ValueError("delivery admission is owned by another request; inspect delivery-requests")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def patch_binding(record):
    return digest({k: record.get(k) for k in ("files", "runtime", "test", "review", "delivery_plan")})


def validate_source(home, spec):
    source = promotion.materialize(home, Path(spec["repo"]), spec["sha"])
    paths = promotion.boundary.changed_paths(spec["repo"], spec["previous"], spec["sha"])
    if spec.get("patch"):
        record = wall_patch.status(Path(home), spec["patch"])
        if record.get("phase") != "reviewed" or not record.get("review", {}).get("clear") or record.get("test", {}).get("code") != 0:
            raise ValueError("queued strong patch needs a fresh clear reviewed record")
        if patch_binding(record) != spec["patch_binding"]:
            raise ValueError("reviewed patch or delivery plan changed since request")
        if not set(paths).issubset(record["files"]):
            raise ValueError("review does not cover every changed committed path")
        promotion.bind_reviewed_source(source, record)
        plan = record.get("delivery_plan") or {}
        if not all(plan.get(k) for k in ("activate", "observe", "revert_observe")):
            raise ValueError("strong request needs reviewed activation and recovery plan")
        wall_patch.validate_plan(Path(home), record, plan["activate"], plan["observe"])
    elif promotion.classification(paths) != "ordinary":
        raise ValueError("high-impact request requires a reviewed strong patch")
    else:
        if spec["command_artifacts"] != wall_patch.command_artifacts(Path(home), [spec["check"], spec["observe"]]):
            raise ValueError("delivery command artifacts changed since request")
    return source


def enqueue(home, repo, sha, *, previous, role, key, check=None, observe=None, patch=None):
    from .roles import declared
    from . import work_boundary
    home, repo = Path(home), Path(repo).resolve()
    if role not in declared(home):
        raise ValueError("delivery request needs a declared author role")
    if not isinstance(key, str):
        raise ValueError("delivery request must originate inside a wake")
    validate_slug(key)
    snapshot = read_json(yielding.snapshot_path(home, key))
    if not snapshot or snapshot.get("role") != role:
        raise ValueError("delivery request must belong to an actual wake")
    work_boundary.full_sha(repo, sha); work_boundary.full_sha(repo, previous)
    spec = dict(repo=str(repo), sha=sha, previous=previous, patch=patch)
    if patch:
        spec["patch_binding"] = patch_binding(wall_patch.status(home, patch))
    else:
        spec.update(check=check, observe=observe,
                    command_artifacts=wall_patch.command_artifacts(home, [check, observe]))
        if not check or not observe:
            raise ValueError("ordinary request needs check and observe commands")
    validate_source(home, spec)
    identity = digest(dict(spec=spec, role=role, key=key))
    path = location(home, identity)
    if path.exists():
        return read_json(path)  # never reset a failed/UNKNOWN request by submitting it again
    row = dict(version=1, id=identity, role=role, key=key, spec=spec,
               state="queued", created_at=time.time())
    write_json(path, row)
    return row


def heartbeat(home, problem=None):
    from . import signal_store
    if SIGNAL not in signal_store.registry(home):
        signal_store.register(home, SIGNAL, max_age=300, subscribers=["genome"])
    signal_store.observe(home, SIGNAL, "RED" if problem else "GREEN", now=time.time(),
                         source="delivery-consumer")


def notify(home, row, text):
    from .failure import default_notify
    default_notify(home, row["role"], f"Delivery request {row['id']}: {text}. Evidence: {location(home, row['id'])}")


def resume(home):
    from . import wake
    from .roles import declared
    following = wake.next_role(home, declared(home), now=time.time())
    if following:
        wake.systemd_starter(following)


@contextmanager
def token_environment(token):
    previous = os.environ.get("MISHE_DELIVERY_TOKEN")
    os.environ["MISHE_DELIVERY_TOKEN"] = token
    try:
        yield
    finally:
        if previous is None: os.environ.pop("MISHE_DELIVERY_TOKEN", None)
        else: os.environ["MISHE_DELIVERY_TOKEN"] = previous


def consume(home):
    """At most one request per pass; no model calls and no automatic effect replay."""
    home = Path(home)
    root = home / "work/delivery-requests"
    if not root.exists() and not admission_path(home).exists():
        return []
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a+") as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return []
        if admission_path(home).exists():
            marker = read_json(admission_path(home))
            row = read_json(location(home, marker["id"]))
            if row["state"] != "UNKNOWN":
                row.update(state="UNKNOWN", error="Consumer interrupted; descendants and effects require reconciliation")
                write_json(location(home, row["id"]), row)
                notify(home, row, row["error"])
            heartbeat(home, row["error"])
            return []
        if (home / "hold").exists():
            return []
        rows = [read_json(path) for path in root.glob("*.json")]
        if any(not isinstance(r, dict) or r.get("version") != 1 or
               r.get("id") != digest(dict(spec=r.get("spec"), role=r.get("role"), key=r.get("key")))
               for r in rows):
            heartbeat(home, "Malformed delivery request; preserve original evidence")
            raise ValueError("malformed delivery request")
        heartbeat(home, next((r.get("error", "Unresolved delivery") for r in rows if r["state"] == "UNKNOWN"), None))
        for row in sorted(rows, key=lambda r: r["created_at"]):
            if row["state"] != "queued": continue
            receipt = read_json(yielding.receipt_path(home, row["key"]))
            if not receipt or receipt.get("role") != row["role"] or receipt.get("key") != row["key"]: continue
            # Use the existing exclusion only to claim admission. Activation
            # children may independently acquire that same dispatch lock.
            try:
                with promotion.delivery_lock(home):
                    if (home / "hold").exists() or DispatchStore(home / "dispatch").open_records():
                        return []
                    # Existing direct patch callers hold this before their
                    # activation child takes dispatch. Never wait in reverse
                    # lock order: defer if a patch is already in flight.
                    with (home / ".wall-patch.lock").open("a+") as patch_lock:
                        try: fcntl.flock(patch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError: return []
                        marker = dict(id=row["id"], token=uuid.uuid4().hex, pid=os.getpid())
                        write_json(admission_path(home), marker)
            except promotion.DeliveryBusy:
                return []
            terminal = False
            try:
                validate_source(home, row["spec"])
                row.update(state="running", started_at=time.time())
                write_json(location(home, row["id"]), row)
                with token_environment(marker["token"]):
                    spec = row["spec"]
                    if spec.get("patch"):
                        record = wall_patch.status(home, spec["patch"])
                        plan = record["delivery_plan"]
                        wall_patch.apply(home, spec["patch"], plan["activate"], plan["observe"])
                        wall_patch.verify(home, spec["patch"])
                        record = wall_patch.status(home, spec["patch"])
                        row.update(state="delivered" if wall_patch.verified_delivery(record) else "UNKNOWN",
                                   patch=spec["patch"])
                    else:
                        record = promotion.promote(home, Path(spec["repo"]), spec["sha"], previous=spec["previous"],
                                                   check=spec["check"], observe=spec["observe"], request_id=row["id"])
                        row.update(state="delivered" if record["phase"] == "delivered" else
                                   "UNKNOWN" if record["phase"] == "UNKNOWN" else "failed", release_id=record["id"])
                terminal = row["state"] != "UNKNOWN"
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                # Validation failure before execution has no effect. After an
                # attempt begins, only explicit recovered evidence is safe.
                recovered = False
                if row["state"] == "running" and row["spec"].get("patch"):
                    record = wall_patch.status(home, row["spec"]["patch"])
                    if record.get("phase") == "reverted":
                        # Restoring bytes/activation alone is not functional
                        # recovery. Observe the old caller with the bound plan.
                        with token_environment(marker["token"]):
                            try:
                                probe = wall_patch.run(home, row["spec"]["patch"], "request-recovery-observe",
                                                       record["delivery_plan"]["revert_observe"])
                            except (OSError, ValueError, subprocess.SubprocessError) as recovery_error:
                                probe = dict(code=None, error=str(recovery_error))
                        row["recovery_observation"] = probe
                        recovered = probe["code"] == 0
                elif row["state"] == "running":
                    attempts = [read_json(p) for p in (home / "work/releases").glob("*.json")]
                    attempts = [r for r in attempts if isinstance(r, dict) and r.get("request_id") == row["id"]]
                    # promote saves activation_started_at before any switch.
                    # A preflight rejection (e.g. superseded previous pin) is
                    # a failed request, not an unknown live effect.
                    recovered = not any(r.get("activation_started_at") for r in attempts)
                terminal = row["state"] != "running" or recovered
                row.update(state="failed" if terminal else "UNKNOWN", error=str(exc))
            finally:
                row["ended_at"] = time.time()
                write_json(location(home, row["id"]), row)
                if terminal and read_json(admission_path(home)) == marker:
                    admission_path(home).unlink()
            heartbeat(home, None if terminal else "Delivery effect UNKNOWN; admission retained")
            try:
                notify(home, row, row["state"] + (": " + row["error"] if row.get("error") else ""))
            finally:
                if terminal: resume(home)
            return [row]
        return []
