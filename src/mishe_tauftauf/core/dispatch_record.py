"""The dispatch record's own text format.

This is the boundary for the one external format the core owns: the JSON
object a supervisor writes next to its state so a successor can read what the
previous process left behind.

Everything that reads that text lives here — the role-name grammar, the JSON
decode and the field validation.  A record that does not satisfy the grammar
is refused with a reason, never repaired and never read as an absent record:
treating unreadable state as "nothing happened" is how one effect becomes
two.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: A role name that can name a file without escaping the store directory.
ROLE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")

#: The states a dispatch record may hold.
STATES = ("pending", "started", "completed", "failed", "skipped", "unknown", "reconciled")


class DispatchRecordError(ValueError):
    """The record's text is not a dispatch record."""


def valid_role(role: Any) -> bool:
    """Whether ``role`` may name a record file."""
    return isinstance(role, str) and bool(ROLE_NAME_RE.fullmatch(role))


def decode_record(raw: Any) -> dict:
    """Decode a record's text into plain values.

    The returned dictionary holds only the fields a dispatch record may
    carry, each of the right type.  Anything else is refused.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DispatchRecordError(f"dispatch record is not UTF-8: {exc}") from exc
    if not isinstance(raw, str):
        raise DispatchRecordError("dispatch record must be text")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DispatchRecordError(f"dispatch record is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DispatchRecordError("dispatch record must be a JSON object")
    role = data.get("role")
    key = data.get("key")
    state = data.get("state")
    detail = data.get("detail", "")
    pid = data.get("pid")
    attempt = data.get("attempt", 1)
    if not valid_role(role):
        raise DispatchRecordError("dispatch record role is not a plain role name")
    if not isinstance(key, str) or not key:
        raise DispatchRecordError("dispatch record key must be a nonempty string")
    if state not in STATES:
        raise DispatchRecordError(f"dispatch record state {state!r} is not a known state")
    if not isinstance(detail, str):
        raise DispatchRecordError("dispatch record detail must be text")
    if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int)):
        raise DispatchRecordError("dispatch record pid must be an integer or absent")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise DispatchRecordError("dispatch record attempt must be a positive integer")
    return {"role": role, "key": key, "state": state, "detail": detail,
            "pid": pid, "attempt": attempt}


def encode_record(record: dict) -> str:
    """Encode a record's fields as its canonical text."""
    return json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"


def receipt_is_unknown(receipt: Any) -> bool:
    """Whether a canonical tick receipt reports an unknown observation.

    The canonical tick writes ``UNKNOWN seed <role> ...`` when the
    observation it needed was stale, missing or unreadable.  That is the
    difference between "nothing was due" and "the reading could not be
    trusted", and the worker must not treat them the same way.
    """
    if not isinstance(receipt, str):
        return False
    return receipt.startswith("UNKNOWN ")


def build_parser(description: str, cmd_status, cmd_dispatch, cmd_reconcile,
                 cmd_supervise=None):
    """The core's own argument grammar.

    The core's command line is a small dialect of its own, so it is decoded
    here rather than in the caller.  The plant's CLI keeps its own grammar.
    """
    import argparse

    root = argparse.ArgumentParser(prog="mishe-core", description=description)
    root.add_argument("--home", type=Path, required=True,
                      help="the site home this supervisor acts for")
    root.add_argument("--state", default=None,
                      help="the dispatch record directory (default: HOME/core-dispatch)")
    root.add_argument("--session", default="mishe-self-development-current",
                      help="the tmux session the role's pane lives in")
    root.add_argument("--role", default="genome", help="the role to act for")
    sub = root.add_subparsers(dest="core_command", required=True)

    status = sub.add_parser("status", help="what the durable record says")
    status.set_defaults(func=cmd_status)

    dispatch = sub.add_parser("dispatch", help="run one turn for the role")
    dispatch.add_argument("--key", required=True,
                          help="the durable identity of this work")
    dispatch.add_argument("--notes", required=True,
                          help="the settlement notes the turn ends with")
    dispatch.add_argument("--command", nargs="+", required=True,
                          help="the command the turn runs")
    dispatch.add_argument("--result", choices=("changed", "verified", "blocked"),
                          default="verified")
    dispatch.add_argument("--continue", dest="continue_task", action="store_true")
    dispatch.add_argument("--timeout", type=float, default=900.0)
    dispatch.add_argument("--self-pick-seconds", type=float, default=0)
    dispatch.set_defaults(func=cmd_dispatch)

    reconcile = sub.add_parser("reconcile", help="close an open dispatch")
    reconcile.add_argument("--detail", required=True,
                            help="what was checked, in prose")
    reconcile.add_argument("--key", default=None,
                            help="the dispatch this reconciliation names")
    reconcile.set_defaults(func=cmd_reconcile)

    if cmd_supervise is not None:
        supervise = sub.add_parser("supervise",
                                    help="run the supervisor as a resident")
        supervise.add_argument("--notes", required=True,
                               help="the settlement notes each turn ends with")
        supervise.add_argument("--command", nargs="+", required=True,
                               help="the command each turn runs")
        supervise.add_argument("--result", choices=("changed", "verified", "blocked"),
                               default="verified")
        supervise.add_argument("--key-prefix", default="core-",
                               help="the prefix of each turn's durable key")
        supervise.add_argument("--interval", type=float, default=15.0)
        supervise.add_argument("--timeout", type=float, default=900.0)
        supervise.add_argument("--self-pick-seconds", type=float, default=0)
        supervise.set_defaults(func=cmd_supervise)
    return root


def decode_arguments(parser, argv):
    """Decode the core's own arguments."""
    return parser.parse_args(argv)


def process_group_members(pgid: int) -> list[int]:
    """The live pids in one process group.

    The kernel's own procfs table is the only authority on which processes
    are still running: the group leader may have exited and its pid may be
    gone, while its children are still alive.  A zombie is not running
    work, so it is not a member.
    """
    import os
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    members = []
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="utf-8") as handle:
                fields = handle.read().rsplit(")", 1)[1].split()
        except (OSError, UnicodeError):
            continue
        # After the comm field: state, ppid, pgrp, ...
        if len(fields) < 3:
            continue
        if fields[2] != str(pgid):
            continue
        if fields[0] == "Z":
            continue
        members.append(int(name))
    return members
