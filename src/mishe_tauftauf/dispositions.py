"""Machine-readable dispositions and edge-triggered alert state.

A watcher that re-sends the same finding every interval trains its resolver to
ignore it. A disposition is the resolver's recorded answer ("known benign until
T, here is the evidence") that every watcher can read back; it expires, so a
stale judgement returns the finding instead of hiding it. Parked roles are the
same record with kind ``parked``: an intentional absence reads as PARKED, not
as a permanent UNKNOWN, and a service missing on a role that is not parked
stays a problem.

Records live under ``SITE/dispositions/``. Nothing here deletes evidence: a
dispositioned finding is still listed in its watcher's snapshot.
"""
from __future__ import annotations

import string
import time
from pathlib import Path

from . import model_boundary as boundary

KINDS = ("benign", "known", "parked")
KEY_CHARS = frozenset(string.ascii_letters + string.digits + "._:-")
# Re-send an unresolved finding after these ages (seconds since first seen).
# The last step then repeats daily. Each step is addressed to a wider resolver.
LADDER = (3600, 6 * 3600, 24 * 3600)
REPEAT = 24 * 3600


def _path(home: Path, key: str) -> Path:
    if not (0 < len(key) <= 128 and set(key) <= KEY_CHARS and key[0] not in ".-:_"):
        raise ValueError("invalid disposition key")
    return Path(home) / "dispositions" / (key.replace(":", "_") + ".json")


def park(home, role, owner, evidence, *, ttl, now=None):
    """Declare ``role`` intentionally absent until the record expires."""
    record = set_disposition(home, "parked:" + role, "parked", owner, evidence, ttl=ttl, now=now, role=role)
    return record


def set_disposition(home, key, kind, owner, evidence, *, ttl, now=None, role=None):
    """Record a resolver's answer. ``ttl`` (seconds) is required: no open-ended mutes."""
    if kind not in KINDS:
        raise ValueError("kind must be one of " + ", ".join(KINDS))
    if not (owner and evidence):
        raise ValueError("owner and evidence are required")
    if not 0 < ttl <= 30 * 24 * 3600:
        raise ValueError("ttl must be positive and at most 30 days")
    now = time.time() if now is None else now
    if (kind == "parked") != bool(role):
        raise ValueError("a parked record names its role, and only a parked record does")
    record = {"key": key, "kind": kind, "owner": owner, "evidence": evidence,
              "created": now, "recheck_at": now + ttl}
    if role:
        record["role"] = role
    boundary.save(_path(home, key), record)
    return record


def active(home, key, *, now=None):
    """The unexpired record for ``key``, else None. Unreadable records are None
    (the finding then stays visible), never an implicit mute."""
    now = time.time() if now is None else now
    try:
        record = boundary.load(_path(home, key))
        if not _well_formed(record) or record["key"] != key or not now < record["recheck_at"]:
            return None
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _well_formed(record) -> bool:
    """A record is a disposition only when every field a reader relies on is present.

    A parked record with no owner/evidence, or whose role does not match its key,
    must not park a role: that would hide a missing role instead of surfacing it.
    """
    if not isinstance(record, dict) or record.get("kind") not in KINDS:
        return False
    if not isinstance(record.get("key"), str) or not record["key"]:
        return False
    if not all(isinstance(record.get(f), str) and any(c not in " \t\r\n" for c in record[f])
               for f in ("owner", "evidence")):
        return False
    for f in ("created", "recheck_at"):
        value = record.get(f)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    if record["kind"] == "parked":
        role = record.get("role")
        if not isinstance(role, str) or not role or record["key"] != "parked:" + role:
            return False
    elif "role" in record:
        return False
    return True


def parked(home, role, *, now=None):
    return active(home, "parked:" + role, now=now) is not None


def parked_roles(home, *, now=None):
    """Roles with an unexpired parked record."""
    return sorted(row["role"] for row in listing(home, now=now)
                  if row.get("kind") == "parked" and "role" in row and not row["expired"])


def clear(home, key):
    _path(home, key).unlink(missing_ok=True)


def listing(home, *, now=None):
    now = time.time() if now is None else now
    rows = []
    root = Path(home) / "dispositions"
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            record = boundary.load(path)
            if not _well_formed(record):
                raise ValueError("malformed disposition record")
            record["expired"] = not now < record["recheck_at"]
            rows.append(record)
        except (OSError, ValueError, KeyError, TypeError):
            rows.append({"file": path.name, "state": "unreadable"})
    return rows


def edge(state, key, signature, *, now=None):
    """Decide whether a finding is worth a message now.

    ``state`` is the persisted dict (mutated). Returns ``(level, first)`` when a
    message is due, else ``None``. Level 0 is the first sighting or a changed
    signature; levels 1.. are the escalation ladder by age, then one per day.
    The caller sends, then persists ``state``.
    """
    now = time.time() if now is None else now
    row = state.get(key)
    if row is None or row["signature"] != signature:
        state[key] = {"signature": signature, "first": now, "level": 0, "sent": now}
        return 0, now
    age = now - row["first"]
    due = sum(1 for step in LADDER if age >= step)
    if age >= LADDER[-1]:
        due += int((age - LADDER[-1]) // REPEAT)
    if due > row["level"]:
        row["level"], row["sent"] = due, now
        return due, row["first"]
    return None


def resolve(state, key):
    state.pop(key, None)


def register(sub):
    import json
    parser = sub.add_parser("disposition", help="Record or inspect a resolver's expiring answer to a finding")
    cmd = parser.add_subparsers(dest="disposition_command", required=True)

    def run(args):
        from .model_change import site_home
        home = site_home(args.home)
        if args.disposition_command == "set":
            out = set_disposition(home, args.key, args.kind, args.owner, args.evidence, ttl=args.ttl_hours * 3600)
        elif args.disposition_command == "park":
            out = park(home, args.role, args.owner, args.evidence, ttl=args.ttl_hours * 3600)
        elif args.disposition_command == "clear":
            clear(home, args.key)
            out = {"cleared": args.key}
        else:
            out = listing(home)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    p = cmd.add_parser("set")
    p.add_argument("key", help="e.g. model-change:<id>")
    p.add_argument("kind", choices=("benign", "known"))
    p2 = cmd.add_parser("park", help="Declare a role intentionally absent until the record expires")
    p2.add_argument("role")
    for q in (p, p2):
        q.add_argument("--owner", required=True)
        q.add_argument("--evidence", required=True)
        q.add_argument("--ttl-hours", type=float, required=True, help="when the answer expires and the finding returns")
        q.set_defaults(func=run)
    p = cmd.add_parser("clear"); p.add_argument("key"); p.set_defaults(func=run)
    p = cmd.add_parser("list"); p.set_defaults(func=run)
