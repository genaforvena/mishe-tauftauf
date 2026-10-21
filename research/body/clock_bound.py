#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Measure what the delivered lane can honestly say about a phone's clock.

Purpose
-------
A node's clock offset (host wall minus phone wall) decides whether an
observation's absolute time can be trusted.  For the Note3 the plant measures
it directly with ``adb shell date +%s``; the Redmi10 has no adb path from this
host, so its clock-domain document says ``status="unavailable"`` and the
projection reports the node as ``absent``.

This instrument answers a narrower, answerable question from the bytes that
*are* delivered: **how far can the delivered lane bound the offset, and what
does it prove about the phone's clock?**

What the delivered lane yields
------------------------------
Every delivered row carries the phone's own wall clock (whole seconds) and the
host's journal carries the receipt time of the POST that delivered it.  For one
paired row::

    observed = host_receipt - device_epoch = skew + latency,   latency >= 0

so ``skew <= observed`` and the **smallest** observed value over a stable
window is an *upper bound* on the skew: the phone's clock is at most that far
behind the host.  The lower side is **not** bounded, because a constant
transport delay and a constant clock offset produce identical delivered bytes.
This instrument therefore never publishes an offset, only the one-sided bound,
and it says so in the document it emits.

Two hazards it measures instead of assuming
-------------------------------------------
* **Reboot.** ``phone_elapsed_ms`` is a monotonic clock since boot.  A reset
  means the device rebooted, so the phone's wall clock was re-read and any
  comparison of device epochs across that boundary is meaningless.  A detector
  that compares epochs across a reboot reports a clock jump that never
  happened.
* **Wall-clock step.** ``device_epoch - phone_elapsed_ms/1000`` is the phone's
  wall clock relative to its own monotonic clock.  It is constant while the
  wall clock is healthy; a step in it is an NTP correction inside a session.

What it does not do
-------------------
It never writes into another plant's site, never touches a device, and never
publishes a two-sided interval: the delivered lane cannot produce one, and a
document that claimed one would be a fabricated reading.

Sources (read-only)
--------------------
``<space>/body/queues/<node>.observations.log``  the delivered rows
``<space>/body/queues/writes.jsonl``             the host's accepted-POST journal
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: The source string the emitted document carries.
SOURCE = "research/body/clock_bound.py"
#: The method string the emitted document carries: what produced the numbers.
METHOD = "delivered-row-epoch-vs-host-receipt"
#: The device epoch is whole seconds, so every pairing carries this much slack.
QUANTIZATION_S = 1.0
#: How long the emitted reading is treated as current.
VALIDITY_SECONDS = 3600.0
#: The longest reason the producer's reader will carry into a rendered pane.
MAX_REASON_CHARS = 200

#: The clock-domain document's keys, as the producer's reader defines them.
#: A document with a key outside this set is not a reading.
DOCUMENT_KEYS = frozenset({
    "node", "source", "method", "status", "observed_at_epoch_s",
    "validity_seconds", "samples", "quantization_s", "rtt_max_s",
    "offset_min_s", "offset_max_s", "offset_nominal_s",
    "device_serial", "device_model", "host_ntp_synchronized", "jump", "reason",
})

ROW_RE = re.compile(r"^(\d+) (\d+) (.*)$")
ELAPSED_RE = re.compile(r"phone_elapsed_ms=(\d+)")
SESSION_RE = re.compile(r"session=([0-9a-zA-Z-]{8,})")

#: A monotonic-clock drop larger than this is a reboot, not a slow sample.
REBOOT_DROP_MS = 60_000
#: A wall-relative drift larger than this is a wall-clock step inside a session.
STEP_DRIFT_S = 3.0


@dataclass(frozen=True)
class Receipt:
    """One accepted POST, as the host's journal records it."""
    host_epoch: float
    records: int
    node: str
    kind: str


@dataclass(frozen=True)
class Row:
    """One delivered observation row."""
    seq: int
    device_epoch: int
    body: str

    @property
    def kind(self) -> str:
        return self.body.split(" ", 1)[0]

    @property
    def elapsed_ms(self) -> int | None:
        match = ELAPSED_RE.search(self.body)
        return int(match.group(1)) if match else None

    @property
    def session_id(self) -> str | None:
        match = SESSION_RE.search(self.body)
        return match.group(1) if match else None


@dataclass
class Pair:
    """A delivered row with the host receipt that delivered it."""
    host_epoch: float
    seq: int
    device_epoch: int
    body: str

    @property
    def observed(self) -> float:
        """host receipt minus device epoch: the skew plus a non-negative delay."""
        return self.host_epoch - self.device_epoch


@dataclass
class Session:
    """One run of the phone app, from its ``hello`` row to the next one."""
    session_id: str | None
    pairs: list[Pair] = field(default_factory=list)
    reboots: list[int] = field(default_factory=list)
    #: True when this session opened after the phone rebooted.
    opened_after_reboot: bool = False

    @property
    def observed(self) -> list[float]:
        return [pair.observed for pair in self.pairs]

    @property
    def skew_upper_s(self) -> float:
        """The one-sided bound: the phone clock is at most this far behind."""
        return min(self.observed)

    @property
    def drift_s(self) -> float:
        """How far the wall clock moved against the phone's own monotonic clock.

        Zero (within the whole-second quantization) means the wall clock did
        not step inside this session.
        """
        values = []
        for pair in self.pairs:
            row = Row(pair.seq, pair.device_epoch, pair.body)
            elapsed = row.elapsed_ms
            if elapsed is None:
                continue
            values.append(pair.device_epoch - elapsed / 1000.0)
        if len(values) < 2:
            return 0.0
        return max(values) - min(values)

    def summary(self) -> dict:
        observed = sorted(self.observed)
        count = len(observed)
        return {
            "session": self.session_id,
            "rows": count,
            "host_span_s": (self.pairs[-1].host_epoch - self.pairs[0].host_epoch)
                           if count else 0.0,
            "device_span_s": (self.pairs[-1].device_epoch - self.pairs[0].device_epoch)
                             if count else 0,
            "observed_min_s": observed[0] if count else None,
            "observed_median_s": statistics.median(observed) if count else None,
            "observed_p95_s": observed[min(count - 1, int(0.95 * count))] if count else None,
            "observed_max_s": observed[-1] if count else None,
            "skew_upper_s": observed[0] if count else None,
            "skew_lower_s": None,
            "wall_drift_s": self.drift_s,
            "reboots": list(self.reboots),
            "opened_after_reboot": self.opened_after_reboot,
            "clock_step": bool(self.drift_s > STEP_DRIFT_S),
        }


def parse_receipts(text: str, node: str | None = None) -> list[Receipt]:
    """The host's accepted-POST journal, in file order.

    A malformed line is skipped rather than guessed at: the journal is the
    host's own record and a line that does not parse is not a delivery.
    """
    receipts: list[Receipt] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            document = json.loads(line)
        except ValueError:
            continue
        if not isinstance(document, dict):
            continue
        stamp = document.get("ts")
        records = document.get("records")
        if not isinstance(stamp, str) or not isinstance(records, int):
            continue
        if isinstance(records, bool) or records < 0:
            continue
        try:
            moment = datetime.fromisoformat(stamp).timestamp()
        except ValueError:
            continue
        if node is not None and document.get("node") != node:
            continue
        receipts.append(Receipt(moment, records,
                                str(document.get("node")), str(document.get("kind"))))
    return receipts


def parse_rows(text: str) -> list[Row]:
    """The delivered rows, in file order."""
    rows: list[Row] = []
    for line in text.splitlines():
        match = ROW_RE.match(line)
        if not match:
            continue
        rows.append(Row(int(match.group(1)), int(match.group(2)), match.group(3)))
    return rows


def pair_rows(rows: list[Row], receipts: list[Receipt]) -> tuple[list[Pair], int]:
    """Pair each delivered row with the receipt that delivered it.

    The pairing walks **backward from the live tail**: every receipt states how
    many rows its POST appended, so the last receipt owns the last N rows, the
    one before it owns the N before that, and so on.  Rows the host wrote
    itself are left unpaired at the head, and their number is reported rather
    than absorbed -- a reader that silently absorbs them mis-pairs the history.
    """
    chunks: list[tuple[Receipt, list[Row]]] = []
    index = len(rows) - 1
    for receipt in reversed(receipts):
        count = receipt.records
        if count <= 0:
            continue
        if index - count + 1 < 0:
            break
        chunks.append((receipt, rows[index - count + 1: index + 1]))
        index -= count
    pairs: list[Pair] = []
    for receipt, chunk in reversed(chunks):
        for row in chunk:
            pairs.append(Pair(receipt.host_epoch, row.seq, row.device_epoch, row.body))
    return pairs, index + 1


def split_sessions(pairs: list[Pair]) -> list[Session]:
    """Split the paired rows into the phone app's sessions.

    A ``hello`` row opens a session.  A monotonic-clock drop inside a session
    is a reboot: the phone's wall clock was re-read, so nothing before that
    drop may be compared with anything after it.
    """
    sessions: list[Session] = []
    current: Session | None = None
    last_elapsed: int | None = None
    for pair in pairs:
        row = Row(pair.seq, pair.device_epoch, pair.body)
        if row.kind == "hello":
            current = Session(row.session_id)
            sessions.append(current)
            last_elapsed = None
        if current is None:
            current = Session(None)
            sessions.append(current)
        elapsed = row.elapsed_ms
        if (elapsed is not None and last_elapsed is not None
                and elapsed < last_elapsed - REBOOT_DROP_MS):
            current.reboots.append(pair.seq)
        if elapsed is not None:
            last_elapsed = elapsed
        current.pairs.append(pair)
    _mark_reboots_between_sessions(sessions)
    return sessions


def _elapsed_values(session: Session) -> list[int]:
    values = []
    for pair in session.pairs:
        row = Row(pair.seq, pair.device_epoch, pair.body)
        elapsed = row.elapsed_ms
        if elapsed is not None:
            values.append(elapsed)
    return values


def _mark_reboots_between_sessions(sessions: list[Session]) -> None:
    """Mark a session that opened after the device rebooted.

    The monotonic clock is the device's own witness of a boot: it only resets
    when the device rebooted, never when the app restarted.  A session whose
    first monotonic reading is far below the previous session's last one
    therefore opened on a new boot, and the phone's wall clock was re-read at
    that moment -- so a device epoch before that boundary and one after it are
    not comparable.
    """
    for older, newer in zip(sessions, sessions[1:]):
        older_values = _elapsed_values(older)
        newer_values = _elapsed_values(newer)
        if not older_values or not newer_values:
            continue
        if min(newer_values) < max(older_values) - REBOOT_DROP_MS:
            newer.opened_after_reboot = True


def measure(rows: list[Row], receipts: list[Receipt]) -> dict:
    """The full delivered-lane reading for one node."""
    pairs, unpaired = pair_rows(rows, receipts)
    sessions = split_sessions(pairs)
    return {
        "paired_rows": len(pairs),
        "unpaired_head_rows": unpaired,
        "sessions": [session.summary() for session in sessions],
        "session_objects": sessions,
    }


def reason_text(measurement: dict) -> str:
    """A bounded reason naming the route, the bound and the limit."""
    sessions = measurement["session_objects"]
    if not sessions or not sessions[-1].pairs:
        return "delivered-row lane: no paired rows"
    current = sessions[-1]
    bound = current.skew_upper_s
    label = current.session_id or "unknown-session"
    text = (f"delivered-row lane: host-device <= {bound:+.3f}s "
            f"(one-sided upper bound, {len(current.pairs)} rows, session {label}); "
            f"lower side unresolved without a device-side clock read")
    return text[:MAX_REASON_CHARS]


def clock_bound_document(node: str, measurement: dict, now: float) -> dict:
    """The clock-domain document this lane can honestly publish.

    ``status`` is ``unavailable`` and every offset is null: the delivered lane
    bounds the skew from above only, and a document that carried an offset
    would be a reading the lane cannot support.
    """
    sessions = measurement["session_objects"]
    current = sessions[-1] if sessions else None
    rows = len(current.pairs) if current else 0
    newest = current.pairs[-1].host_epoch if (current and current.pairs) else now
    document = {
        "node": node,
        "source": SOURCE,
        "method": METHOD,
        "status": "unavailable",
        "observed_at_epoch_s": newest,
        "validity_seconds": VALIDITY_SECONDS,
        "samples": rows,
        "quantization_s": QUANTIZATION_S,
        "rtt_max_s": None,
        "offset_min_s": None,
        "offset_max_s": None,
        "offset_nominal_s": None,
        "device_serial": None,
        "device_model": None,
        "host_ntp_synchronized": None,
        "jump": None,
        "reason": reason_text(measurement),
    }
    if not set(document) <= DOCUMENT_KEYS:
        raise ValueError("document carries a key the reader does not accept")
    return document


def render_summary(node: str, measurement: dict) -> str:
    """The human-readable reading of one measurement."""
    lines = [f"node {node}: paired rows {measurement['paired_rows']}, "
             f"unpaired head rows {measurement['unpaired_head_rows']}"]
    for entry in measurement["sessions"]:
        lines.append(
            f"  session {entry['session']}: rows={entry['rows']} "
            f"host_span={entry['host_span_s']/3600:.3f}h "
            f"observed[min/median/p95/max]="
            f"{entry['observed_min_s']:.3f}/{entry['observed_median_s']:.3f}/"
            f"{entry['observed_p95_s']:.3f}/{entry['observed_max_s']:.3f}s")
        lines.append(
            f"    skew upper bound (host-device) <= {entry['skew_upper_s']:+.3f}s; "
            f"lower side unresolved; wall-clock drift vs monotonic clock "
            f"{entry['wall_drift_s']:.3f}s "
            f"({'STEP' if entry['clock_step'] else 'no step'}); "
            f"reboots={entry['reboots'] or 'none'}; "
            f"opened after reboot={entry['opened_after_reboot']}")
    return "\n".join(lines)


def write_document(path: Path, document: dict) -> None:
    """Write the document atomically, so a reader never sees half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".clock-bound-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, sort_keys=True, indent=1)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--space", default="/home/mesh-home/android-body/.mishe-tauftauf",
                        help="the site home holding body/queues/")
    parser.add_argument("--node", default="redmi10")
    parser.add_argument("--emit-document", default=None,
                        help="write the clock-domain document here")
    parser.add_argument("--json", action="store_true", help="print the measurement as JSON")
    arguments = parser.parse_args(argv)

    space = Path(arguments.space)
    queues = space / "body" / "queues"
    rows_path = queues / f"{arguments.node}.observations.log"
    writes_path = queues / "writes.jsonl"
    try:
        rows_text = rows_path.read_text(encoding="utf-8", errors="replace")
        writes_text = writes_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print(f"cannot read the delivered lane: {error}", file=sys.stderr)
        return 2

    rows = parse_rows(rows_text)
    receipts = [receipt for receipt in parse_receipts(writes_text)
                if receipt.node == arguments.node and receipt.kind == "observations"]
    measurement = measure(rows, receipts)
    document = clock_bound_document(arguments.node, measurement, datetime.now(timezone.utc).timestamp())

    if arguments.json:
        printable = dict(measurement)
        printable.pop("session_objects", None)
        printable["document"] = document
        print(json.dumps(printable, indent=1, sort_keys=True))
    else:
        print(render_summary(arguments.node, measurement))
        print(f"reason: {document['reason']}")
    if arguments.emit_document:
        write_document(Path(arguments.emit_document), document)
        print(f"document written: {arguments.emit_document}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
