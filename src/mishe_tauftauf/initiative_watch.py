"""Deterministic review signals and durable addressed notices, never a work score.

Routing contract: a condition is routed once to its resolver and its owner, a
changed condition is routed again as a new version, an unrouted condition
escalates once to the escalation peers, and later reminders address the owner
alone on a backing-off schedule measured from the last route. Detection, the
immutable notice archive and the pending outbox are not narrowed by this.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

from . import initiative
from .chat_protocol import ProtocolError, decode_lifecycle
from .feed import Feed, entry_timestamp, parse_feed
from .session_watch import notify
from .substrate_watch import _atomic_bytes

VERSION = 1
SOURCE = "sensor/initiative-watch"
MAX_TAPE_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 250000
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024
MAX_EVENTS = 4096
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
AGED_SECONDS = 3600
CONTINUATION_SECONDS = 1800
ESCALATE_SECONDS = 600
REMINDER_SECONDS = 1800
MAX_REMINDER_SECONDS = 14400
STAGES = ("initial", "changed", "escalation", "reminder")


def _time(value=None):
    value = datetime.now(timezone.utc) if value is None else value
    if isinstance(value, str):
        # The tape stamps every instant with a trailing "Z"; Python 3.10's
        # fromisoformat rejects that suffix, so decode it the way the rest of
        # the plant does (initiative._time, feed.entry_timestamp).
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def _read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("nonregular or oversized monitor input")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("monitor input exceeds bound")
    return raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(raw):
    """Literal boundary: bounded UTF-8 monitor state/event JSON, no duplicate keys."""
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _save(path, value):
    value["sha256"] = _digest({key: item for key, item in value.items() if key != "sha256"})
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode() + b"\n"
    if len(raw) > (MAX_EVENT_BYTES if path.parent.name == "initiative-watch-events" else MAX_STATE_BYTES):
        raise ValueError("monitor persistence bound exceeded")
    _atomic_bytes(path, raw)


def _role(value):
    return (isinstance(value, str) and 0 < len(value) <= 128
            and all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in value))


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 4096


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _shape(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError("invalid monitor object shape")


def _sealed(value):
    if not _hash(value.get("sha256")) or value["sha256"] != _digest({k: v for k, v in value.items() if k != "sha256"}):
        raise ValueError("monitor checksum mismatch")


def _finding(value):
    _shape(value, "id kind role ref summary primary escalation action")
    if (value["kind"] not in {"aged-wake", "continuation-review", "prediction", "unknown"}
            or not all(_text(value[key]) for key in ("id", "ref", "summary", "action"))
            or (value["role"] is not None and not _role(value["role"]))):
        raise ValueError("invalid monitor finding")
    for key in ("primary", "escalation"):
        if (not isinstance(value[key], list) or not value[key] or len(value[key]) > 4
                or any(not _role(role) for role in value[key]) or len(set(value[key])) != len(value[key])):
            raise ValueError("invalid monitor recipients")
    if value["kind"] in {"aged-wake", "continuation-review"} and value["role"] is None:
        raise ValueError("missing finding subject")
    expected = _make(value["kind"], value["role"], value["ref"], value["summary"])
    if any(value[key] != expected[key] for key in ("id", "primary", "escalation", "action")):
        raise ValueError("invalid finding identity/routing")


def _incident(value):
    _shape(value, "finding first_seen last_notice stage")
    _finding(value["finding"])
    first, last = _time(value["first_seen"]), _time(value["last_notice"])
    if first > last or value["stage"] not in set(STAGES):
        raise ValueError("invalid incident timer")


def _event(value):
    _shape(value, "version event_id created_at incident recipients sha256")
    _sealed(value)
    if type(value["version"]) is not int or value["version"] != VERSION or not _hash(value["event_id"]):
        raise ValueError("invalid monitor event")
    _incident(value["incident"])
    if (_time(value["created_at"]) != _time(value["incident"]["last_notice"])
            or value["event_id"] != _digest({k: value[k] for k in ("created_at", "incident", "recipients")})):
        raise ValueError("invalid monitor event identity")
    finding = value["incident"]["finding"]
    expected = _accepted_routes(finding, value["incident"]["stage"])
    if tuple(value["recipients"]) not in expected:
        raise ValueError("invalid event routing")
    return value


def _validate(row):
    _shape(row, "version generated_at state producer_at producer_state findings incidents deliveries pending problems state_damage sha256")
    _sealed(row)
    if type(row["version"]) is not int or row["version"] != VERSION or row["state"] not in {"OBSERVED", "ATTENTION", "UNKNOWN"}:
        raise ValueError("invalid monitor version/state")
    now = _time(row["generated_at"])
    if row["producer_at"] is not None and _time(row["producer_at"]) > now:
        raise ValueError("future producer")
    if row["producer_state"] not in {None, "OBSERVED", "PARTIAL", "UNKNOWN"}:
        raise ValueError("invalid producer state")
    if not isinstance(row["findings"], list) or len(row["findings"]) > MAX_EVENTS:
        raise ValueError("invalid findings")
    for finding in row["findings"]:
        _finding(finding)
    if len({f["id"] for f in row["findings"]}) != len(row["findings"]):
        raise ValueError("duplicate findings")
    if not isinstance(row["incidents"], dict) or len(row["incidents"]) > MAX_EVENTS:
        raise ValueError("invalid incidents")
    for key, incident in row["incidents"].items():
        _incident(incident)
        if key != incident["finding"]["id"] or _time(incident["last_notice"]) > now:
            raise ValueError("invalid incident identity/time")
    if set(row["incidents"]) != {f["id"] for f in row["findings"]}:
        raise ValueError("incomplete incident coverage")
    if any(row["incidents"][f["id"]]["finding"] != f for f in row["findings"]):
        raise ValueError("inconsistent incident evidence")
    if not isinstance(row["deliveries"], dict) or len(row["deliveries"]) > MAX_EVENTS:
        raise ValueError("invalid deliveries")
    for key, receipts in row["deliveries"].items():
        if (not _hash(key) or not isinstance(receipts, dict) or len(receipts) > 4
                or any(not _role(role) or type(seq) is not int or seq <= 0 for role, seq in receipts.items())):
            raise ValueError("invalid delivery receipt")
    if not isinstance(row["pending"], list) or len(row["pending"]) > MAX_EVENTS:
        raise ValueError("invalid pending deliveries")
    seen = set()
    for item in row["pending"]:
        _shape(item, "event_id recipients")
        if (not _hash(item["event_id"]) or item["event_id"] in seen
                or not isinstance(item["recipients"], list) or not item["recipients"]
                or len(item["recipients"]) > 4 or len(set(item["recipients"])) != len(item["recipients"])
                or any(not _role(role) for role in item["recipients"])):
            raise ValueError("invalid pending delivery")
        seen.add(item["event_id"])
        if any(role in row["deliveries"].get(item["event_id"], {}) for role in item["recipients"]):
            raise ValueError("pending recipient already has a receipt")
    if (not isinstance(row["problems"], list) or any(not _text(p) for p in row["problems"])
            or (row["state_damage"] is not None and not _text(row["state_damage"]))):
        raise ValueError("invalid monitor problems")
    unknown = bool(row["problems"] or row["state_damage"] or row["pending"]
                   or any(f["kind"] == "unknown" for f in row["findings"]))
    expected = "UNKNOWN" if unknown else "ATTENTION" if row["findings"] else "OBSERVED"
    if row["state"] != expected or (row["producer_at"] is None and not unknown):
        raise ValueError("inconsistent monitor state")
    return row


def _make(kind, role, ref, summary):
    if kind == "aged-wake":
        primary, escalation = ["health", role], ["genome", "witness"]
        action = "Inspect this exact wake; record an actual outcome or send a bounded blocker with evidence to the escalation peers."
    elif kind == "continuation-review":
        primary, escalation = ["health" if role == "genome" else "genome", role], ["witness", "health"]
        action = "Review the continuation chain semantically; publish a digest-checked outcome or escalate the evidenced blocker to peers."
    elif kind == "prediction":
        primary, escalation = [role or "health", "genome"], ["health", "witness"]
        action = "Check the canonical prediction against actual evidence and publish its disposition, or escalate the evidenced blocker to peers."
    else:
        primary, escalation = ["health"], ["genome", "witness"]
        action = "Restore the named evidence or delivery coverage with a bounded repair; escalate an unavailable prerequisite to peers."
    identity = f"{kind}:{role}:{ref}" if kind == "aged-wake" else f"{kind}:{role}" if kind == "continuation-review" else f"{kind}:{ref}"
    return {"id": identity, "kind": kind, "role": role, "ref": ref, "summary": summary,
            "primary": list(dict.fromkeys(primary)), "escalation": list(dict.fromkeys(escalation)), "action": action}


def _owner(finding):
    """The role that can act on the finding; escalation peers are not owners."""
    role = finding.get("role")
    if _role(role):
        return role
    ref = finding.get("ref") or ""
    if ref.startswith("outcomes:") and _role(ref.split(":", 1)[1]):
        return ref.split(":", 1)[1]
    return finding["primary"][0]


def _route(finding, stage):
    """Recipients for one stage: route once, escalate once, remind the owner."""
    owner = _owner(finding)
    primary = list(finding["primary"])
    escalation = list(finding["escalation"])
    if stage in ("initial", "changed"):
        return list(dict.fromkeys(primary + [owner]))
    if stage == "escalation":
        return list(dict.fromkeys(primary + [owner] + escalation))
    return [owner]


def _accepted_routes(finding, stage):
    """Current routing plus the legacy routing kept valid for archived notices."""
    legacy = list(finding["primary"]) if stage == "initial" else list(dict.fromkeys(finding["primary"] + finding["escalation"]))
    return {tuple(_route(finding, stage)), tuple(legacy)}


def _reminder_interval(reminders):
    """Seconds to wait after the given number of reminders were already routed."""
    return min(REMINDER_SECONDS * (2 ** max(0, reminders)), MAX_REMINDER_SECONDS)


def _reminders(history):
    """How many reminders the current version of a condition has already routed.

    The initial notice and the escalation are not reminders; counting them
    would delay the first reminder past its interval and shorten the
    30m, 1h, 2h, 4h schedule the contract names.
    """
    return sum(1 for event in history if event["incident"]["stage"] == "reminder")


def _finding_events(events, key, first_seen=None):
    """The routed notices of one condition; optionally only its current version."""
    history = [event for event in events.values() if event["incident"]["finding"]["id"] == key]
    if first_seen is not None:
        moment = _time(first_seen)
        history = [event for event in history if _time(event["incident"]["first_seen"]) == moment]
    return history


def _latest_event(history):
    if not history:
        return None
    return max(history, key=lambda event: (_time(event["created_at"]), event["event_id"]))


def _routed(deliveries, event, owner=None):
    """Whether the owner of the condition already received this notice."""
    receipts = deliveries.get(event["event_id"], {})
    if owner and owner in event["recipients"]:
        return bool(receipts.get(owner))
    return any(receipts.get(recipient) for recipient in event["recipients"])


def _event_interval(events, event):
    """The interval the next reminder of this condition will wait for."""
    incident = event["incident"]
    notices = _reminders(_finding_events(events, incident["finding"]["id"], incident["first_seen"]))
    return _reminder_interval(notices)


def evaluate(report, entries, *, now=None):
    """Pure review evidence evaluation; caller supplies a canonically checked report."""
    now = _time(now)
    findings, unknown = [], {}

    def gap(ref, reason):
        unknown[ref] = _make("unknown", None, ref, reason)

    if report["history"]["state"] != "OBSERVED":
        gap("history", "Source/history coverage is incomplete; absence of review signals is not established.")
    if report["outcomes"]["state"] != "OBSERVED":
        gap("outcomes", "Outcome receipt/evidence coverage is incomplete; unverified receipts do not suppress review.")
    if report["predictions"]["state"] != "OBSERVED":
        gap("predictions", "Canonical prediction evidence is incomplete.")
    if report["lifecycle_gaps"]:
        gap("lifecycle", "Canonical lifecycle decoding has coverage gaps.")
    pending, continuations = {}, {}
    for entry in entries:
        stamp = entry_timestamp(entry)
        if stamp is None or stamp.tzinfo is None or stamp > now:
            gap("tape-time", "Tape has absent, invalid or future event times.")
            continue
        try:
            control = decode_lifecycle(entry)
        except ProtocolError:
            gap("lifecycle", "Canonical lifecycle decoding has coverage gaps.")
            continue
        if control is None:
            continue
        role = control.role
        if control.kind == "wake":
            if role in pending:
                gap("lifecycle:" + role, "A wake was superseded without its matching settlement.")
            pending[role] = (entry, stamp)
        elif control.kind == "yield":
            wake = pending.get(role)
            if wake is None or wake[0].sequence != control.wake:
                gap("lifecycle:" + role, "A yield has no matching pending wake in available history.")
                continue
            pending.pop(role)
            if control.continue_work and stamp >= now - timedelta(seconds=CONTINUATION_SECONDS):
                continuations.setdefault(role, []).append((entry, stamp))
        # Administrative clear is not a settlement/outcome receipt.
    for role, (entry, stamp) in sorted(pending.items()):
        if (now - stamp).total_seconds() >= AGED_SECONDS:
            findings.append(_make("aged-wake", role, f"chat.log#{entry.sequence}", "Current exact open wake is at least 3600s old; settlement closes only this signal, not the underlying issue."))
    for role, chain in sorted(continuations.items()):
        if len(chain) < 6:
            continue
        first = min(stamp for _, stamp in chain)
        last_sequence = max(entry.sequence for entry, _ in chain)
        if (report["history"].get("last_sequence") or 0) < last_sequence:
            gap("outcomes:" + role, "Producer outcome coverage does not yet cover the continuation chain.")
        receipt = any(item["state"] == "OBSERVED" and item["role"] == role
                      and first <= _time(item["timestamp"]) <= now for item in report["outcomes"]["items"])
        if not receipt:
            findings.append(_make("continuation-review", role, f"chat.log#{chain[0][0].sequence}", "At least six matching yield continuations in 1800s lack a checked outcome since the first; semantic review required, not proof of inactivity."))
    for role, row in report["roles"].items():
        if row["lifecycle_gaps"] or "UNKNOWN" in row["settlements"]:
            gap("lifecycle:" + role, "Role lifecycle coverage is incomplete.")
        if row["outcome_coverage"] != "OBSERVED" or row["outcome_gaps"]:
            gap("outcomes:" + role, "Role outcome coverage is incomplete.")
    for item in report["predictions"]["items"]:
        if item["state"] != "OBSERVED":
            gap(item["ref"], "Prediction replay/evidence is UNKNOWN, not a successful check.")
        elif item["accepted"] and item["open"] and not item["terminal"] and _time(item["check_at"]) < now:
            role = item.get("role")
            if role is not None and not _role(role):
                raise ValueError("invalid prediction role")
            findings.append(_make("prediction", role, item["ref"], "Accepted open canonical prediction is overdue; its check needs actual evidence."))
    # Patch-only producer PARTIAL is deliberately not a monitored review signal.
    return sorted(findings + list(unknown.values()), key=lambda item: item["id"])


@contextmanager
def _lock(home):
    directory = home / "checks"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / ".initiative-watch.lock").open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _archive(home):
    directory = home / "checks/initiative-watch-events"
    if not directory.exists():
        return {}
    events, total = {}, 0
    with os.scandir(directory) as children:
        for child in children:
            if len(events) >= MAX_EVENTS:
                raise ValueError("monitor event archive count bound exceeded")
            raw = _read(Path(child.path), MAX_EVENT_BYTES)
            total += len(raw)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("monitor event archive byte bound exceeded")
            event = _event(_json(raw))
            if child.name != event["event_id"] + ".json":
                raise ValueError("invalid event archive name")
            events[event["event_id"]] = event
    return events


def _retain(home, path):
    raw = _read(path, MAX_STATE_BYTES)
    fingerprint = hashlib.sha256(raw).hexdigest()
    retained = home / "checks/initiative-watch-corrupt" / (fingerprint + ".bin")
    if retained.exists():
        if _read(retained, MAX_STATE_BYTES) != raw:
            raise ValueError("retained state conflicts")
    else:
        _atomic_bytes(retained, raw)
    return "Corrupt prior state retained sha256=" + fingerprint


def _notice(event, *, interval=None):
    incident = event["incident"]
    finding = incident["finding"]
    stage = incident["stage"]
    deadline = _time(incident["first_seen"]) + timedelta(seconds=ESCALATE_SECONDS)
    if stage == "reminder":
        owner = _owner(finding)
        routing = ("Owner=" + owner + "; Resolver=" + owner
                   + "; last route=" + incident["last_notice"]
                   + "; next reminder after " + str(interval) + "s")
        timing = "First seen=" + incident["first_seen"] + "; reminder of the unchanged condition"
    else:
        routing = "Resolver=" + ",".join(finding["primary"]) + "; escalation=" + ",".join(finding["escalation"])
        timing = "First seen=" + incident["first_seen"] + "; evidence/escalation deadline=" + deadline.isoformat()
    return "\n".join([
        "INITIATIVE REVIEW " + stage + " event=" + event["event_id"],
        finding["id"] + " ref=" + finding["ref"], finding["summary"],
        routing,
        timing,
        "Action: " + finding["action"],
        "Clearing this observation does not certify that a semantic issue is fixed.",
    ])


def _send(home, recipient, text):
    return Feed(home).append(SOURCE, f"[dm] to={recipient}\n{text}", once=True)


def _status(row):
    row["state"] = ("UNKNOWN" if row["problems"] or row["state_damage"] or row["pending"]
                    or any(f["kind"] == "unknown" for f in row["findings"])
                    else "ATTENTION" if row["findings"] else "OBSERVED")


def _pending(events, deliveries):
    return [{"event_id": key, "recipients": [r for r in event["recipients"] if r not in deliveries.get(key, {})]}
            for key, event in sorted(events.items())
            if any(r not in deliveries.get(key, {}) for r in event["recipients"])]


def check(home, *, now=None):
    """Observe and route once. Persist evidence/outbox before any addressed append."""
    home, now = Path(home).resolve(), _time(now)
    path = home / "checks/initiative-watch.json"
    with _lock(home):
        events = _archive(home)
        previous, damage = None, None
        try:
            previous = _validate(_json(_read(path, MAX_STATE_BYTES)))
            if _time(previous["generated_at"]) > now:
                raise ValueError("prior state future-dated")
            damage = previous["state_damage"]
        except FileNotFoundError:
            if events:
                damage = "Established monitor state is missing; archived notices retained and retried."
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            # If retention fails, propagate; do not replace the original.
            damage = _retain(home, path)
        deliveries = dict(previous["deliveries"]) if previous else {}
        if any(key not in events or any(role not in events[key]["recipients"] for role in receipts)
               for key, receipts in deliveries.items()):
            raise ValueError("delivery evidence archive missing or inconsistent")
        problems, report = [], None
        try:
            report = initiative.read_snapshot(home, now=now, max_age=180)
            entries = parse_feed(_read(home / "chat.log", MAX_TAPE_BYTES), home=home)
            if len(entries) > MAX_ENTRIES:
                raise ValueError("tape entry bound exceeded")
            last = report["history"].get("last_sequence")
            if last is not None and (type(last) is not int or last > len(entries)):
                raise ValueError("producer history is not covered by available tape")
            findings = evaluate(report, entries, now=now)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            findings = [_make("unknown", None, "producer", "Fresh canonical report/tape evidence unavailable (" + type(exc).__name__ + "); no healthy conclusion.")]
        if damage:
            findings.append(_make("unknown", None, "monitor-state", damage))
        incidents = {}
        prior_incidents = dict(previous["incidents"]) if previous else {}
        # Recover checkpointed timers from immutable notices after interrupted saves.
        for event in sorted(events.values(), key=lambda item: item["created_at"]):
            incident = event["incident"]
            key = incident["finding"]["id"]
            if key not in prior_incidents or _time(incident["last_notice"]) > _time(prior_incidents[key]["last_notice"]):
                prior_incidents[key] = incident
        for finding in findings:
            key = finding["id"]
            prior = prior_incidents.get(key)
            # A prior incident is reusable only if still active at the last checkpoint,
            # or if its event was not yet checkpointed when the process stopped.
            if previous and key not in previous["incidents"] and prior:
                if _time(prior["last_notice"]) <= _time(previous["generated_at"]):
                    prior = None
            stage = None
            changed = prior is not None and _digest(prior["finding"]) != _digest(finding)
            if prior is None or changed:
                # A new or changed condition is routed once; its escalation clock restarts.
                incident = {"finding": finding, "first_seen": now.isoformat(), "last_notice": now.isoformat(),
                            "stage": "initial" if prior is None else "changed"}
                stage = incident["stage"]
                history = []
            else:
                incident = dict(prior, finding=finding)
                # Only the notices of the current condition version pace its reminders.
                history = _finding_events(events, key, prior["first_seen"])
                age = (now - _time(prior["first_seen"])).total_seconds()
                since = (now - _time(prior["last_notice"])).total_seconds()
                if prior["stage"] in ("initial", "changed") and age >= ESCALATE_SECONDS:
                    stage = "escalation"
                elif prior["stage"] in ("escalation", "reminder"):
                    interval = _reminder_interval(_reminders(history))
                    latest = _latest_event(history)
                    if since >= interval and (latest is None or _routed(deliveries, latest, _owner(finding))):
                        stage = "reminder"
                if stage:
                    incident.update(stage=stage, last_notice=now.isoformat())
            incidents[key] = incident
            if stage:
                if len(events) >= MAX_EVENTS:
                    problems.append("Monitor event archive is full; notice cannot be durably queued.")
                    continue
                recipients = _route(finding, stage)
                content = {"created_at": now.isoformat(), "incident": incident, "recipients": recipients}
                event_id = _digest(content)
                event = {"version": VERSION, "event_id": event_id, **content}
                _save(home / "checks/initiative-watch-events" / (event_id + ".json"), event)
                events[event_id] = event
        row = {"version": VERSION, "generated_at": now.isoformat(), "state": "UNKNOWN",
               "producer_at": report["generated_at"] if report else None,
               "producer_state": report["state"] if report else None,
               "findings": findings, "incidents": incidents, "deliveries": deliveries,
               "pending": _pending(events, deliveries), "problems": problems, "state_damage": damage}
        _status(row)
        _save(path, row)
        for event_id, event in sorted(events.items()):
            receipts = deliveries.setdefault(event_id, {})
            for recipient in event["recipients"]:
                if recipient in receipts:
                    continue
                try:
                    receipt = _send(home, recipient, _notice(event, interval=_event_interval(events, event)))
                    receipts[recipient] = receipt.sequence
                    row["pending"] = _pending(events, deliveries)
                    _status(row)
                    _save(path, row)
                except (OSError, ValueError) as exc:
                    problems.append(f"Delivery unavailable recipient={recipient} event={event_id} ({type(exc).__name__})")
        row["pending"] = _pending(events, deliveries)
        _status(row)
        _save(path, row)
        return row


def read_snapshot(home, *, now=None, max_age=180):
    """Strict bounded cache-only read; missing, stale, future or malformed raises."""
    if isinstance(max_age, bool) or not isinstance(max_age, (int, float)) or not math.isfinite(max_age) or max_age < 0:
        raise ValueError("max_age must be finite and nonnegative")
    now = _time(now)
    try:
        row = _validate(_json(_read(Path(home) / "checks/initiative-watch.json", MAX_STATE_BYTES)))
        age = (now - _time(row["generated_at"])).total_seconds()
        if age < 0 or age > max_age:
            raise ValueError("initiative monitor snapshot stale or future-dated")
        if row["state"] != "UNKNOWN" and (row["producer_at"] is None or not 0 <= (now - _time(row["producer_at"])).total_seconds() <= 180):
            raise ValueError("initiative producer snapshot stale or future-dated")
        return row
    except (TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise ValueError("invalid initiative monitor snapshot") from exc


def snapshot_lines(home, *, now=None, max_age=180):
    """Render only the saved monitor cache, without tape reads or wake creation."""
    try:
        row = read_snapshot(home, now=now, max_age=max_age)
    except (OSError, ValueError, UnicodeError):
        return ["INITIATIVE: UNKNOWN — monitor cache missing, stale, corrupt or unreadable"]
    lines = [f"INITIATIVE: {row['state']} checked_at={row['generated_at']} pending={len(row['pending'])}"]
    for finding in row["findings"][:12]:
        incident = row["incidents"][finding["id"]]
        due = _time(incident["first_seen"]) + timedelta(seconds=ESCALATE_SECONDS)
        lines.append(f"{finding['id']} ref={finding['ref']} resolver={','.join(finding['primary'])} stage={incident['stage']} deadline={due.isoformat()} escalation={','.join(finding['escalation'])}: {finding['summary']}")
    if len(row["findings"]) > 12:
        lines.append(f"Additional findings: {len(row['findings']) - 12}")
    for item in row["pending"][:4]:
        lines.append("DELIVERY UNKNOWN event=" + item["event_id"] + " recipients=" + ",".join(item["recipients"]))
    lines.extend("INITIATIVE UNKNOWN: " + problem for problem in row["problems"][:4])
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=60)
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be finite and positive")
    if args.watch:
        notify("READY=1")
    while True:
        try:
            row = check(args.home)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            row = {"state": "UNKNOWN", "reason": "monitor persistence/evidence unavailable (" + type(exc).__name__ + ")"}
        print(json.dumps(row, sort_keys=True, allow_nan=False), flush=True)
        if not args.watch:
            return {"OBSERVED": 0, "ATTENTION": 2, "UNKNOWN": 1}[row["state"]]
        notify("WATCHDOG=1\nSTATUS=" + row["state"] + " deterministic initiative review evidence")
        try:
            # Keep supervision alive even with an interval longer than WatchdogSec.
            remaining = args.interval
            while remaining > 0:
                delay = min(remaining, 5)
                time.sleep(delay)
                remaining -= delay
                notify("WATCHDOG=1")
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
