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

so ``skew <= observed`` and every row bounds the phone's clock from above: the
phone's clock is at most ``observed`` behind the host at that row.  That is a
**per-row** bound; the lower side is **not** bounded, because a constant
transport delay and a constant clock offset produce identical delivered bytes.
This instrument therefore never publishes an offset, only the one-sided bound,
and it says so in the document it emits.

The session-wide bound
----------------------
``device_epoch - phone_elapsed_ms/1000`` is the phone's wall clock against its
own monotonic clock, and it is measured for every row that carries the
monotonic reading.  Writing ``v`` for that value and ``M`` for the monotonic
clock's offset from true time (constant while the device does not reboot), the
phone's clock error at a row is ``M - v``, and ``observed >= M - v`` gives
``M <= min(observed + v)``.  The error is therefore at most
``min(observed + v) - min(v)`` at every row of the session that carries the
monotonic reading.

That bound carries a premise the delivered lane cannot test: it assumes the
phone's monotonic clock tracks true time.  The premise is published with the
bound (:data:`BEHIND_BOUND_PREMISE`), and the premise-free bound - the one at
the freshest delivered sample - is published beside it.

The bound is withheld when the lane cannot support it: when the phone's uptime
counter resets inside the session (a reboot re-reads the wall clock, so the rows
on either side of the reset do not share one monotonic offset), when the
counter falls by more than the wall clock's own quantization when the rows are
ordered by the phone's clock (a reset the delivered order alone would hide),
and when no row carries the monotonic reading at all.

What the bound is for
---------------------
A large ``observed`` value is usually the *lane* being late, not the phone's
clock being wrong, and the two are told apart by the monotonic reading: if
the phone's wall clock tracked its own monotonic clock, then the staleness of
a timestamp is the delivery delay plus a clock error the bound already caps.
So the instrument also publishes the lower bound on the delivery delay that
follows from the clock bound, and the drift of the wall clock against the
monotonic clock, which is what separates the two explanations.

Two hazards it measures instead of assuming
-------------------------------------------
* **Reset.** ``phone_elapsed_ms`` is a monotonic clock since boot.  A reset
  means the device rebooted, so the phone's wall clock was re-read and any
  comparison of device epochs across that boundary is meaningless.  A reset
  and a delivery batch written newest-first look alike in the delivered order
  - the counter falls in both - so a fall that lands on a fresh-boot value is
  treated as ambiguous and withholds the session-wide bound rather than being
  resolved by assumption.
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
#: It has to hold the bound, the unresolved side and the premise the
#: session-wide bound rests on, so it is not cut short of them.  A reason
#: that would not fit is cut at a clause boundary, never inside one.
MAX_REASON_CHARS = 600

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

#: An uptime reading at or below this is a device that has just started, so
#: a decrease that lands on it is a reset.  A decrease that leaves the counter
#: large is a delivery batch written in reverse order, not a restart.
REBOOT_RESET_MS = 600_000
#: A wall-relative drift larger than this is a wall-clock step inside a session.
STEP_DRIFT_S = 3.0
#: When the rows are ordered by the phone's own clock, a fall of the uptime
#: counter larger than this is a reset the delivered order alone would hide.
#: The wall clock is whole seconds, so rows of the same second can differ by
#: about one second of uptime; the tolerance is above that.
MASKED_RESET_TOLERANCE_S = 5.0
#: The window at the end of a session that carries the phone's current clock
#: level.
LEVEL_WINDOW_S = 900.0

#: The premise the session-wide bound rests on.  It is published with the
#: bound because the delivered lane cannot test it: nothing in the lane says
#: how fast the phone's monotonic clock runs against true time, and nothing in
#: the lane says that a row without a monotonic reading did not sit on a
#: reset.
BEHIND_BOUND_PREMISE = (
    "the phone's wall clock is measured against its own monotonic clock, and "
    "the bound assumes that monotonic clock tracks true time and that the "
    "rows carrying the reading share one monotonic offset, neither of which "
    "the delivered lane can test")


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
    #: The rows where the phone's uptime counter fell onto a fresh-boot value
    #: in the delivered order: a reset and a reverse-order batch look alike
    #: there, so these rows are ambiguous rather than classified.
    ambiguous_resets: list[int] = field(default_factory=list)
    #: The rows where the uptime counter falls once the rows are ordered by
    #: the phone's own clock: a reset the delivered order alone would hide.
    masked_resets: list[int] = field(default_factory=list)

    @property
    def observed(self) -> list[float]:
        return [pair.observed for pair in self.pairs]

    def covered_pairs(self) -> list[tuple["Pair", int]]:
        """The rows that carry the phone's own monotonic reading.

        A row without that reading cannot be placed against the phone's
        monotonic clock, so it is left out of the derivation rather than
        assumed to be zero.
        """
        covered = []
        for pair in self.pairs:
            elapsed = Row(pair.seq, pair.device_epoch, pair.body).elapsed_ms
            if elapsed is not None:
                covered.append((pair, elapsed))
        return covered

    def detect_resets(self) -> None:
        """Where the phone's uptime counter fell, in two orders.

        A delivery batch is written newest first, so the counter falls inside
        a batch while the device keeps running.  A reboot resets the counter
        to a fresh-boot value and re-reads the wall clock, and the rows on
        either side of it do not share one monotonic offset.  The two cannot
        be told apart by the delivered order alone, so a fall that lands on a
        fresh-boot value is recorded as **ambiguous** and withholds the
        session-wide bound instead of being resolved by assumption.

        The second order is the phone's own clock: when the rows are sorted by
        the phone's wall clock, a counter that falls by more than the wall
        clock's own quantization is a reset the delivered order would have
        hidden.
        """
        self.ambiguous_resets = []
        self.masked_resets = []
        covered = self.covered_pairs()
        for index in range(1, len(covered)):
            previous, current = covered[index - 1][1], covered[index][1]
            if current < previous and current <= REBOOT_RESET_MS:
                self.ambiguous_resets.append(covered[index][0].seq)
        by_device_time = sorted(covered, key=lambda item: item[0].device_epoch)
        for index in range(1, len(by_device_time)):
            previous, current = by_device_time[index - 1][1], by_device_time[index][1]
            if (current < previous - MASKED_RESET_TOLERANCE_S * 1000
                    and current <= REBOOT_RESET_MS):
                # A counter that falls by more than the wall clock's own
                # quantization *and* lands on a fresh-boot value is a reset
                # the delivered order hid.  A fall that leaves the counter
                # large is a wall clock that stepped backwards, which the
                # drift measurement already accounts for.
                self.masked_resets.append(by_device_time[index][0].seq)

    @property
    def level_now_s(self) -> float | None:
        """The smallest observed value in the last window of the session.

        This is the phone's current clock level: the smallest delay the
        delivery lane has shown recently, which is the closest the lane can
        come to the phone's clock error now.
        """
        if not self.pairs:
            return None
        last = self.pairs[-1].host_epoch
        window = [pair.observed for pair in self.pairs
                  if pair.host_epoch >= last - LEVEL_WINDOW_S]
        return min(window) if window else None

    @property
    def level_change_s(self) -> float | None:
        """How much the observed level moved between the session and its end.

        ``observed`` is the clock error plus the delivery delay, so this is a
        description of how much later the lane's rows were received than at
        the session's tightest row - it is not a measurement of the phone's
        clock, which is what :attr:`drift_s` measures.
        """
        level_now = self.level_now_s
        if level_now is None or not self.observed:
            return None
        return level_now - min(self.observed)

    def wall_values(self) -> list[tuple["Pair", float]]:
        """The rows that carry the phone's monotonic reference.

        Each entry is the row and the wall clock's value against that
        reference: ``device epoch - elapsed``.  A row without the phone's
        monotonic reading cannot be placed against true time, so it is left
        out rather than assumed to be zero.
        """
        return [(pair, pair.device_epoch - elapsed / 1000.0)
                for pair, elapsed in self.covered_pairs()]

    @property
    def drift_s(self) -> float:
        """How far the wall clock moved against the phone's own monotonic clock.

        Zero (within the whole-second quantization) means the wall clock did
        not step inside this session.
        """
        values = [value for _, value in self.wall_values()]
        if len(values) < 2:
            return 0.0
        return max(values) - min(values)

    @property
    def behind_upper_s(self) -> float | None:
        """The bound on how far the phone's clock was behind, session-wide.

        The delivered lane gives, for every paired row, ``observed = host
        receipt - device epoch``, and ``observed`` is the phone's clock error
        (how far it is behind the host) plus a transport delay that cannot be
        negative.  So every row bounds the phone's clock from above.

        The wall clock's movement against the phone's own monotonic clock is
        measured, so the movement can be accounted for.  Writing ``v`` for
        the wall clock against the monotonic reference and ``M`` for the
        monotonic clock's offset from true time (constant while the device
        does not reboot), the phone's clock error at a row is ``M - v`` and
        ``M <= min(observed + v)``.  The error is therefore at most
        ``min(observed + v) - min(v)`` at every row that carries the
        monotonic reading.

        The premise is named in :data:`BEHIND_BOUND_PREMISE`: it assumes the
        phone's monotonic clock tracks true time, which the delivered lane
        cannot test.

        The bound is published only when the session supports it.  The
        derivation needs ``M`` to be one value across the rows it covers, and
        a reset of the phone's uptime counter breaks that: a reboot re-reads
        the wall clock, so the rows before it and the rows after it do not
        share one monotonic offset.  A reset and a delivery batch written
        newest-first look alike in the delivered order, so a fall that lands
        on a fresh-boot value is treated as ambiguous
        (:attr:`ambiguous_resets`), and a fall that shows up only when the
        rows are ordered by the phone's own clock is a reset the delivered
        order would have hidden (:attr:`masked_resets`).  Either one withholds
        the bound; only the bound at the freshest sample then stands.
        """
        if self.ambiguous_resets or self.masked_resets:
            return None
        values = self.wall_values()
        if not values:
            return None
        m_max = min(pair.observed + value for pair, value in values)
        return m_max - min(value for _, value in values)

    @property
    def session_wide_withheld_reason(self) -> str | None:
        """Why no session-wide bound holds, or ``None`` when one does.

        The reason is published with the bound so that a reader is never
        left to guess which of the possible reasons applies.  It is a short
        phrase; the session summary carries the measurements behind it.
        """
        if self.ambiguous_resets:
            return ("the phone's uptime counter fell onto a fresh-boot value "
                    "inside the session, which a reset and a reverse-order "
                    "batch cannot be told apart by")
        if self.masked_resets:
            return ("the phone's uptime counter falls when the rows are "
                    "ordered by the phone's own clock, so the session spans a "
                    "reset")
        if not self.wall_values():
            return "the session carries no phone monotonic reading"
        return None

    @property
    def freshest_pair(self) -> Pair | None:
        """The freshest sample of the session, or ``None`` when the lane
        cannot establish one.

        The freshest sample is the row with the greatest phone uptime, not
        the last row in the queue: a delivery batch is written in reverse
        device-time order, so the last row of a batch is the oldest row of
        that batch.

        Two situations make the lane unable to say which row was produced
        last, and the figure is withheld rather than guessed:

        * a **reset inside the session.**  The uptime counter is only
          chronological within one boot, and a reset re-reads the wall
          clock, so the rows on either side of it cannot be ordered against
          each other at all.  A row from before the reset carries the
          larger counter, and picking it would name a stale row as the
          freshest one.
        * **no monotonic reading at all.**  The device epoch is the phone's
          wall clock, and a wall clock that was re-read backwards makes the
          greatest epoch the *oldest* row, so the fallback would publish a
          bound about the wrong row.

        The premise-free per-row bound at the session's tightest row does
        not need a chronology, so it is published instead.
        """
        if self.ambiguous_resets or self.masked_resets:
            return None
        covered = self.covered_pairs()
        if not covered:
            return None
        return max(covered, key=lambda item: (item[1], item[0].device_epoch))[0]

    @property
    def delivery_latency_lower_s(self) -> float | None:
        """The lower bound on how late the host received the rows.

        ``observed = delay + clock error``, and the clock error is at most
        :attr:`behind_upper_s`, so ``delay >= observed - behind_upper_s``.
        The largest such value over the session is how late the lane was at
        its worst - which is what tells a reader that a timestamp's staleness
        is the lane's lateness rather than the phone's clock being wrong.
        """
        bound = self.behind_upper_s
        if bound is None:
            return None
        covered = self.wall_values()
        if not covered:
            return None
        return max(pair.observed for pair, _ in covered) - bound

    @property
    def behind_upper_at_last_sample_s(self) -> float | None:
        """The exact bound at the freshest delivered sample.

        ``observed`` at that sample is the phone's clock error plus a
        non-negative delay, so the error is at most ``observed`` there.  This
        holds with no premise at all, unlike :attr:`behind_upper_s`.

        It is ``None`` when the lane cannot establish which row was produced
        last - see :attr:`freshest_pair`.
        """
        freshest = self.freshest_pair
        return freshest.observed if freshest is not None else None

    @property
    def last_receipt_epoch(self) -> float | None:
        """When the host last received a row of this session.

        This is the lane's own clock, and it is established: the host knows
        when it received the rows.  It is not the same thing as the age of
        the freshest *sample*, which depends on when the phone produced that
        row.
        """
        if not self.pairs:
            return None
        return max(pair.host_epoch for pair in self.pairs)

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
            "behind_upper_s": self.behind_upper_s,
            "behind_upper_at_last_sample_s": self.behind_upper_at_last_sample_s,
            "behind_lower_s": None,
            "behind_level_now_s": self.level_now_s,
            "level_change_s": self.level_change_s,
            "monotonic_reference_rows": len(self.wall_values()),
            "premise": BEHIND_BOUND_PREMISE,
            "wall_drift_s": self.drift_s,
            "delivery_latency_lower_s": self.delivery_latency_lower_s,
            "session_wide_withheld_reason": self.session_wide_withheld_reason,
            "ambiguous_resets": list(self.ambiguous_resets),
            "masked_resets": list(self.masked_resets),
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

    A ``hello`` row opens a session.  A reboot inside a session resets the
    phone's uptime counter and re-reads its wall clock, so nothing before
    that may be compared with anything after it.
    """
    sessions: list[Session] = []
    current: Session | None = None
    last_elapsed: int | None = None
    last_device_epoch = -1
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
                and elapsed < last_elapsed
                and elapsed <= REBOOT_RESET_MS
                and pair.device_epoch > last_device_epoch):
            # A reboot resets the phone's uptime counter *and* re-reads its
            # wall clock, so the device epoch moves forward past the row
            # before it.  A decrease that leaves the counter large, or a row
            # whose device epoch does not advance, is a delivery batch
            # written in reverse device-time order - nothing restarted.
            current.reboots.append(pair.seq)
        if elapsed is not None:
            last_elapsed = elapsed
        last_device_epoch = pair.device_epoch
        current.pairs.append(pair)
    for session in sessions:
        session.detect_resets()
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
    first monotonic reading shows a device that has been up for only a few
    minutes therefore opened on a new boot, and the phone's wall clock was
    re-read at that moment -- so a device epoch before that boundary and one
    after it are not comparable.

    A decrease that leaves the counter large is not a boot: a delivery batch
    is written in reverse device-time order, so the counter decreases inside
    a batch without anything having restarted.
    """
    for older, newer in zip(sessions, sessions[1:]):
        newer_values = _elapsed_values(newer)
        if not newer_values:
            continue
        if min(newer_values) <= REBOOT_RESET_MS:
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
    """A bounded reason naming the route, the bound and the limit.

    The bound is one-sided: it says how far the phone's clock was *behind*
    the host, never how far it may be ahead.  The session-wide figure is the
    one the phone's own monotonic reference makes derivable, and it is
    published with its premise.

    The reason is the only prose a reader of the emitted document gets, so
    it carries the premise and the unresolved side itself: a reader who sees
    only the document must not be able to mistake the bound for a measured
    offset.
    """
    sessions = measurement["session_objects"]
    if not sessions or not sessions[-1].pairs:
        return "delivered-row lane: no paired rows"
    current = sessions[-1]
    label = (current.session_id or "unknown-session")[:8]
    rows = len(current.pairs)
    bound = current.behind_upper_s
    if bound is None:
        withheld = (current.session_wide_withheld_reason
                    or "the lane does not support one")
        at_sample = current.behind_upper_at_last_sample_s
        if at_sample is None:
            text = (f"delivered-row lane: the lane does not establish which row "
                    f"of session {label} was produced last ({rows} rows), so no "
                    f"freshest-sample bound is published; the tightest bound the "
                    f"session's rows support is {current.observed_min_s:+.3f}s "
                    f"behind the host, at the row that produced it; how far "
                    f"ahead the phone's clock may be is unresolved; no "
                    f"session-wide bound: {withheld}")
        else:
            text = (f"delivered-row lane: at the last delivered sample the "
                    f"phone's clock was at most {at_sample:+.3f}s behind the "
                    f"host (session {label}, {rows} rows); how far ahead it may "
                    f"be is unresolved; no session-wide bound: {withheld}")
    else:
        latency = current.delivery_latency_lower_s
        covered = len(current.wall_values())
        text = (f"delivered-row lane: the phone's clock was at most {bound:+.3f}s "
                f"behind the host at every one of the {covered} rows of session "
                f"{label} that carry the phone's own monotonic reading; how far "
                f"ahead it may be is unresolved; premise: that monotonic clock "
                f"tracks true time and that those rows share one monotonic "
                f"offset, neither of which the lane can test")
        if latency is not None:
            text += (f"; the rows were delivered at least {latency:.1f}s after "
                     f"the phone produced them")
    if len(text) <= MAX_REASON_CHARS:
        return text
    # A reason that does not fit is cut at a clause boundary, so it never
    # ends inside a clause and never loses a number without saying so.
    cut = text.rfind("; ", 0, MAX_REASON_CHARS)
    return text[:cut] if cut > 0 else text[:MAX_REASON_CHARS]


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


def _seconds(value: float | None) -> str:
    """A signed second value, or ``n/a`` where the lane cannot produce one."""
    return "n/a" if value is None else f"{value:+.3f}s"


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
            f"    behind bound (session-wide) <= "
            f"{_seconds(entry['behind_upper_s'])}; "
            f"behind at last sample <= "
            f"{_seconds(entry['behind_upper_at_last_sample_s'])}; "
            f"lower side unresolved; min(host-device) = "
            f"{entry['observed_min_s']:+.3f}s; wall-clock drift vs monotonic "
            f"clock {entry['wall_drift_s']:.3f}s "
            f"({'STEP' if entry['clock_step'] else 'no step'}); "
            f"monotonic rows={entry['monotonic_reference_rows']}; "
            f"delivery latency >= {_seconds(entry['delivery_latency_lower_s'])}; "
            f"ambiguous resets={entry['ambiguous_resets'] or 'none'}; "
            f"masked resets={entry['masked_resets'] or 'none'}; "
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
