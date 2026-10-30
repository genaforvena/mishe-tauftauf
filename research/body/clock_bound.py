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
#: How stale a delivered row has to be before the profile counts it as stale.
#: A minute is where a row stops being usable as a present observation of a
#: person, and it is a parameter because the body that owns the queue sets
#: its own cadence.
STALE_ROW_THRESHOLD_S = 60.0
#: The tolerance a consumer of the human's own analysis needs from a row's
#: age: the age of one delivered row relative to another, to within this
#: many seconds.
CORRECTION_TOLERANCE_S = 1.0
#: How long the emitted reading is treated as current.
VALIDITY_SECONDS = 3600.0
#: The longest reason the producer's reader will carry into a rendered pane.
#: The bound on this is the reader's, not this producer's: the android-body
#: reader replaces a longer reason with a bare class token
#: (``body/runtime/watch.py``: ``CLOCK_DOMAIN_REASON_MAX_CHARS = 200``, and
#: ``body/runtime/clock_offset.py``'s own validator caps a published reason
#: at the same 200).  A reason past that limit is not shortened - it is
#: *dropped*, so every fact it carried is lost.  The reason is therefore
#: built to fit, and the facts that do not fit are carried by the typed field
#: instead.
MAX_REASON_CHARS = 200

#: The version of the typed clock-bound field.  A consumer that does not
#: know this version must ignore the field rather than guess its shape.
CLOCK_BOUND_VERSION = 1

#: The clock-domain document's keys, as the producer's reader defines them.
#: A document with a key outside this set is not a reading.
DOCUMENT_KEYS = frozenset({
    "node", "source", "method", "status", "observed_at_epoch_s",
    "validity_seconds", "samples", "quantization_s", "rtt_max_s",
    "offset_min_s", "offset_max_s", "offset_nominal_s",
    "device_serial", "device_model", "host_ntp_synchronized", "jump", "reason",
    "clock_bound",
})

ROW_RE = re.compile(r"^(\d+) (\d+) (.*)$")
ELAPSED_RE = re.compile(r"phone_elapsed_ms=(\d+)")
SESSION_RE = re.compile(r"session=([0-9a-zA-Z-]{8,})")

#: An uptime reading at or below this is a device that has just started, so
#: a decrease that lands on it is a reset.  A decrease that leaves the counter
#: large is a delivery batch written in reverse order, not a restart.
REBOOT_RESET_MS = 600_000

#: The most rows one POST may carry.  The app posts at most 128 records
#: (``Protocol.MAX_BATCH``), so a receipt cannot own more rows than that, and
#: a lost receipt shifts its neighbour by at most that many rows.
MAX_BATCH_ROWS = 128
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

#: The premises the session-wide bound rests on, one claim each.  They are
#: published with the bound because the delivered lane cannot test either:
#: nothing in the lane says how fast the phone's monotonic clock runs
#: against true time, and nothing in the lane says that a row without a
#: monotonic reading did not sit on a reset.
BEHIND_BOUND_PREMISES = (
    "the phone's monotonic clock tracks true time",
    "the rows carrying the reading share one monotonic offset",
)

#: The same two premises as one sentence, for the prose reason and for the
#: measurement summary.  The typed field carries them as the list above,
#: because a consumer checks each claim separately.
BEHIND_BOUND_PREMISE = (
    "the phone's wall clock is measured against its own monotonic clock, and "
    "the bound assumes that monotonic clock tracks true time and that the "
    "rows carrying the reading share one monotonic offset, neither of which "
    "the delivered lane can test")


@dataclass(frozen=True)
class Receipt:
    """One accepted POST, as the host's journal records it.

    ``byte_count`` is the host's own statement of how many bytes the POST
    body carried.  It is a *witness* independent of ``records``: the host
    writes both, but they come from different measurements - the body length
    it read, and the number of lines it appended - so a pairing that
    contradicts one of them contradicts the other.  The journal does not
    always carry it, so it is optional.
    """
    host_epoch: float
    records: int
    node: str
    kind: str
    byte_count: int | None = None


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
    def lateness_profile(self) -> dict:
        """How the lane's own lateness is spread over the session's rows.

        ``observed`` is the phone's clock error plus a non-negative transport
        delay, so its spread over the rows is the spread of the two
        together.  One worst-case figure hides what a consumer actually has
        to cope with: a lane that is usually prompt and occasionally hours
        behind is a different animal from a lane that is uniformly late, and
        only the first can be aged row by row.

        The profile says how stale the typical row is, how stale the tail is,
        and whether one constant correction could serve the whole session.
        That last statement is about *relative* age - how well a row's age
        relative to the session's tightest row is known - because the
        absolute age of a row is not recoverable from the lane at all: a
        constant transport delay and a constant clock offset produce
        identical bytes (see :data:`BEHIND_BOUND_PREMISE`).

        The residual carries :data:`QUANTIZATION_S` on top of the spread: the
        device epoch is whole seconds, so the difference between two rows'
        ages carries up to that much rounding slack of its own, and the
        residual is what a consumer's age estimate can be wrong by.  It is
        reported as the *worst case* over the session, not as an average.
        """
        values = sorted(pair.observed for pair, _ in self.wall_values())
        if not values:
            return {}
        count = len(values)
        stale = sum(1 for value in values if value > STALE_ROW_THRESHOLD_S)
        spread = values[-1] - values[0]
        residual = spread + QUANTIZATION_S
        return {
            "rows": count,
            "observed_min_s": values[0],
            "observed_median_s": statistics.median(values),
            "observed_p95_s": values[min(count - 1, int(0.95 * count))],
            "observed_max_s": values[-1],
            "spread_s": spread,
            "stale_threshold_s": STALE_ROW_THRESHOLD_S,
            "stale_rows": stale,
            "stale_fraction": stale / count,
            "relative_age_residual_s": residual,
            "tolerance_s": CORRECTION_TOLERANCE_S,
            "constant_correction_within_tolerance": residual <= CORRECTION_TOLERANCE_S,
            "note": (f"the lane's lateness varies by {spread:.3f} s across the "
                     f"session, so one correction leaves up to {residual:.3f} s "
                     f"of error and a row's age has to come from that row's own "
                     f"receipt"
                     if not residual <= CORRECTION_TOLERANCE_S else
                     "the lane's lateness is steady here, so one correction "
                     "serves the session's rows"),
        }

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
            "lateness": self.lateness_profile,
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

    One damage pattern is *recovered* rather than skipped, because skipping
    it is not neutral.  A line can arrive zero-padded in front of a JSON
    object that is itself intact - the padding stands where the line's own
    beginning was overwritten, and the object behind it is the record the
    host wrote.  Skipping such a line drops one delivery from the journal
    while the rows it delivered stay in the queue, and the pairing that
    walks backward from the live tail then mis-attributes every row back to
    the hole: the damage is silent and it shifts the whole history.
    Recovering the intact object behind the padding is not a guess - the
    object still has to parse, and the pairing is still checked against the
    journal's own byte witness before any reading is published from it.

    The damage is still counted and reported: a reader must see that the
    journal was damaged, not that it was quietly healed.
    """
    receipts: list[Receipt] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        padded = False
        if line and line[0] == "\x00":
            stripped = line.lstrip("\x00").strip()
            if stripped and stripped != line:
                line = stripped
                padded = True
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
        byte_count = document.get("byte_count")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) \
                or byte_count < 0:
            byte_count = None
        receipts.append(Receipt(moment, records,
                                str(document.get("node")), str(document.get("kind")),
                                byte_count))
    return receipts


def count_padded_receipt_lines(text: str) -> int:
    """How many journal lines were zero-padded in front of an intact object.

    The count is published beside the reading, so a repaired lane still
    shows that the journal was damaged.
    """
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and stripped[0] == "\x00":
            remainder = stripped.lstrip("\x00").strip()
            if remainder and remainder != stripped:
                try:
                    json.loads(remainder)
                except ValueError:
                    continue
                count += 1
    return count


def parse_rows(text: str) -> list[Row]:
    """The delivered rows, in file order."""
    rows: list[Row] = []
    for line in text.splitlines():
        match = ROW_RE.match(line)
        if not match:
            continue
        rows.append(Row(int(match.group(1)), int(match.group(2)), match.group(3)))
    return rows


@dataclass(frozen=True)
class Chunk:
    """One POST's worth of delivered rows, with the receipt that delivered them.

    Every row in a chunk was delivered by the same POST, so every row in it
    has the same delivery age.  That is what makes the per-row age table
    compressible: the age belongs to the POST, and the row's own sample clock
    carries the rest.
    """
    receipt: Receipt
    rows: tuple[Row, ...]


def post_body_bytes(row: Row) -> int:
    """How many bytes this row occupied in the POST body that carried it.

    The body line is the device's own clock reading, a space, the row's
    content, and the newline that ends it - the app writes every event as
    ``"<epoch> <content>\\n"`` (``Protocol.event``) and the host counts the
    body bytes it read.  The delivered row keeps the content and the epoch,
    so the same number can be recomputed from the queue, and that recomputed
    number is the witness the pairing is checked against.
    """
    return len(f"{row.device_epoch} {row.body}".encode("utf-8")) + 1


@dataclass(frozen=True)
class Pairing:
    """The paired lane, with what the pairing had to do to get there.

    ``chunks`` are the POSTs and the rows each delivered.  ``unpaired_rows``
    counts the delivered rows that **no** receipt owns: the rows the host
    wrote at the head before the first receipt, and the rows a lost receipt
    left behind.  ``repairs`` records every contradiction the byte witness
    found and what was done about it.

    The repairs are part of the reading, not a footnote to it.  A pairing that
    was corrected - or that could not be corrected - changes which POST a row
    belongs to, and therefore changes every age derived from it.  A reader
    that cannot see the correction cannot tell a repaired history from a
    clean one.
    """
    chunks: list[Chunk]
    unpaired_rows: int
    repairs: tuple[dict, ...] = ()

    @property
    def paired_rows(self) -> int:
        """How many delivered rows a receipt owns."""
        return sum(len(chunk.rows) for chunk in self.chunks)

    @property
    def repaired(self) -> bool:
        """Whether the byte witness forced any change to the pairing."""
        return bool(self.repairs)


def pair_chunks(rows: list[Row], receipts: list[Receipt]) -> tuple[list[Chunk], int]:
    """Group the delivered rows by the POST that delivered them.

    The pairing walks **backward from the live tail**: every receipt states how
    many rows its POST appended, so the last receipt owns the last N rows, the
    one before it owns the N before that, and so on.  Rows the host wrote
    itself are left unpaired at the head, and their number is reported rather
    than absorbed -- a reader that silently absorbs them mis-pairs the history.

    A receipt's row count is checked against the host's **byte witness** before
    it is believed.  The host writes both numbers from two different
    measurements - the body length it read and the number of lines it
    appended - so a receipt whose rows do not add up to its byte count is
    pointing at the wrong rows.  That happens when the journal itself was
    damaged: a lost receipt leaves its delivered rows in the queue with
    nobody claiming them, and the walk would then hand those rows to their
    neighbour and shift every age it derives from them.

    So the walk is corrected, not merely reported: when a receipt contradicts
    the witness, the rows it owns are found by their bytes instead of by their
    count, the rows nobody owns are left unpaired and counted, and the
    correction is published beside the reading.  A reader that keeps a pairing
    it has already proved wrong would be publishing an age it knows to be
    wrong.

    The correction is made **only where it is provable**.  The byte witness
    places a receipt when exactly one run of rows in the neighbourhood sums to
    the bytes the host says it read.  When several runs fit, or none does, the
    witness does not establish which rows the receipt owned, and the count-based
    pairing is kept and the contradiction reported: a wrong guess would move
    rows onto a POST that never carried them, which is a worse failure than
    a reported gap.
    """
    pairing = pair_lane(rows, receipts)
    return pairing.chunks, pairing.unpaired_rows


def pair_lane(rows: list[Row], receipts: list[Receipt]) -> Pairing:
    """The paired lane, with the repairs the byte witness forced.

    See :func:`pair_chunks` for the pairing rule.  This returns the whole
    story: the chunks, the rows no receipt owns, and every contradiction the
    witness found.

    The walk runs **backward from the live tail**, as it always has: the last
    receipt owns the last rows the host appended, the one before it owns the
    rows before those, and the rows the host wrote before the first receipt
    are left unpaired at the head.  The tail is the anchor because it is the
    one end of the queue that no lost receipt can move.

    Each receipt's rows are then checked against the host's **byte witness**.
    A receipt whose rows do not add up to the bytes the host says it read is
    pointing at the wrong rows, and the repair is to find the window that
    does.  A repair is only made where it is **provable** - see
    :func:`_unique_consistent_window` - and the rows of a lost receipt are
    counted as unpaired rather than absorbed by their neighbour.
    """
    chunks: list[Chunk] = []
    repairs: list[dict] = []
    unpaired_rows = 0
    # The tail of the queue is the anchor: the last receipt owns the last
    # rows.  `end` is the exclusive end of the rows still unclaimed.
    end = len(rows)
    for receipt_index in range(len(receipts) - 1, -1, -1):
        receipt = receipts[receipt_index]
        count = receipt.records
        if count <= 0:
            continue
        if end - count < 0:
            break
        owned = list(rows[end - count: end])
        want = receipt.byte_count
        if want is None:
            # No witness: the count is all there is.
            chunks.append(Chunk(receipt, tuple(owned)))
            end -= count
            continue
        got = sum(post_body_bytes(row) for row in owned)
        if got == want:
            chunks.append(Chunk(receipt, tuple(owned)))
            end -= count
            continue
        # The receipt contradicts the witness.  Its rows may be further back:
        # a lost receipt leaves its delivered rows in the queue, and the walk
        # would then hand those rows to the receipt behind it.
        candidates = _windows_by_bytes(rows, end - count, count, want, end)
        if not candidates:
            repairs.append({
                "host_epoch": receipt.host_epoch,
                "records": count,
                "byte_count": want,
                "rows_bytes": got,
                "resolution": "unplaceable",
            })
            chunks.append(Chunk(receipt, tuple(owned)))
            end -= count
            continue
        chosen = _unique_consistent_window(rows, receipts, receipt_index,
                                            candidates)
        if chosen is None:
            repairs.append({
                "host_epoch": receipt.host_epoch,
                "records": count,
                "byte_count": want,
                "rows_bytes": got,
                "resolution": "ambiguous",
                "candidates": len(candidates),
            })
            chunks.append(Chunk(receipt, tuple(owned)))
            end -= count
            continue
        start, last = chosen
        # The rows this receipt does not own are the ones between the end of
        # its repaired window and the walk's boundary: the receipt behind it
        # claimed them, or the lost receipt did, and nobody owns them now.
        # They are counted as unpaired rather than absorbed.
        #
        # The count is the size of that gap, and it cannot be negative: a
        # candidate window is only considered when it lies wholly inside the
        # rows no receipt has claimed yet, so it cannot reach back over the
        # rows the receipt behind it already owns.  A negative count here
        # would mean the repair had double-owned rows, and a reader summing
        # the two would get the wrong history length.
        #
        # The count is measured from the window the repair actually chose,
        # not from the receipt's own row count: the repaired window can hold
        # fewer rows than the receipt claims, and counting by the receipt's
        # count would then drop the difference on the floor - the history
        # would be short by exactly those rows, with nothing to show for it.
        lost = end - last - 1
        repairs.append({
            "host_epoch": receipt.host_epoch,
            "records": count,
            "byte_count": want,
            "rows_bytes": got,
            "resolution": "realigned",
            "first_seq": rows[start].seq,
            "last_seq": rows[last].seq,
            "rows_before": count,
            "rows_owned": last - start + 1,
            "unpaired_rows": lost,
        })
        unpaired_rows += lost
        chunks.append(Chunk(receipt, tuple(rows[start: last + 1])))
        end = start
    chunks.reverse()
    # Rows before the first receipt are the ones the host wrote itself: they
    # are unpaired, and they are reported rather than absorbed.
    unpaired_rows += end
    return Pairing(chunks, unpaired_rows, tuple(repairs))


def _windows_by_bytes(rows: list[Row], position: int, count: int,
                      want: int, claimed_end: int | None = None) -> list[tuple[int, int]]:
    """Every window of ``count`` rows whose bytes are the ones a receipt claims.

    The search is bounded to the neighbourhood of the window the receipt's own
    count would have given it, because that is where the rows are: a lost
    receipt shifts its neighbour by the number of rows it owned, and the shift
    is at most a batch.

    The search also stays **inside the rows no receipt has claimed yet**
    (``claimed_end``).  A window that reached back over rows the receipt
    behind it already owns would claim those rows twice, and a pairing that
    double-owned rows would report a history longer than the queue - the
    orphan count would even come out negative, because the repair would
    appear to give back more rows than the lost receipt took.

    Several windows usually fit.  The rows are small and their sizes repeat,
    so the byte count alone does not say which window the receipt owned --
    on the live lane nine windows fit the damaged receipt's bytes, and four
    of those hold the right number of rows.  The list is returned whole, and
    :func:`_unique_consistent_window` decides between them.
    """
    limit = max(count, MAX_BATCH_ROWS) * 2
    low = max(0, position - limit)
    # The window may reach as far forward as the search radius allows, and
    # only its *start* is limited by the unclaimed region: a window of
    # `count` rows starting at `first` ends at `first + count - 1`, so it
    # stays inside rows ``[0, claimed_end)`` when it starts no later than
    # ``claimed_end - count``.
    first_limit = min(len(rows), position + limit)
    if claimed_end is not None:
        first_limit = min(first_limit, claimed_end - count + 1)
    matches: list[tuple[int, int]] = []
    for first in range(low, max(low, first_limit)):
        running = 0
        for last in range(first, min(len(rows), first + count)):
            running += post_body_bytes(rows[last])
            if running == want:
                matches.append((first, last))
                break
            if running > want:
                break
    return matches


def _unique_consistent_window(rows: list[Row], receipts: list[Receipt],
                              receipt_index: int, candidates: list[tuple[int, int]]
                              ) -> tuple[int, int] | None:
    """The one candidate window the rest of the journal agrees with.

    A damaged receipt cannot be repaired by looking at itself: its byte count
    fits several windows, and nothing in the damaged line says which one it
    was.  What does say is the journal around it.  A candidate is only the
    right one if the receipts around it still agree with their own byte
    witnesses when the walk resumes from it.

    That test is what turns an ambiguous witness into a decidable one, and it
    is available precisely because the damage is local: one lost receipt
    cannot be placed by itself, but it can be placed by what its neighbours
    still agree on.  On the live lane, four windows fit the damaged receipt's
    bytes and row count, and exactly one of them leaves the rest of the
    journal consistent.

    If no candidate leaves the rest of the journal consistent, the answer is
    ``None``: the pairing is left as the count says, and the contradiction is
    reported rather than resolved by a guess.
    """
    consistent = []
    for start, end in candidates:
        if _walk_is_consistent(rows, receipts, receipt_index, start):
            consistent.append((start, end))
    if len(consistent) != 1:
        return None
    return consistent[0]


def _walk_is_consistent(rows: list[Row], receipts: list[Receipt],
                        receipt_index: int, position: int) -> bool:
    """Whether the receipts *before* ``receipt_index`` still match their witnesses.

    The walk is backward, like the pairing itself: from the candidate
    window's start, each earlier receipt must own exactly the rows its own
    count names, and those rows must sum to its byte count.  A receipt
    without a witness is accepted on its count alone.

    The walk stops at the first receipt that disagrees.  It does not need to
    check the whole journal to decide a candidate: a receipt that no longer
    matches its witness means the candidate is wrong, whatever the rest of the
    journal says.
    """
    end = position
    for receipt in reversed(receipts[:receipt_index]):
        count = receipt.records
        if count <= 0:
            continue
        if end - count < 0:
            return False
        window = rows[end - count: end]
        want = receipt.byte_count
        if want is not None and sum(post_body_bytes(row) for row in window) != want:
            return False
        end -= count
    return True


def verify_pairing(chunks: list[Chunk]) -> dict:
    """Check the pairing against the journal's own byte witness.

    Every receipt states how many bytes its POST body carried, and the
    delivered rows carry those same bytes.  The two numbers come from
    different measurements - the body length the host read, and the line
    count it appended - so a pairing that contradicts one contradicts the
    other.  That makes the byte count a witness the pairing can be *tested*
    against, rather than a number that has to be trusted.

    A contradiction is not a smaller number to report: it means the lane's
    row-to-POST attribution is wrong, and every age derived from it is
    wrong with it.  So the verdict is a yes or a no, and the no carries the
    first contradiction so a reader can see where the pairing broke.
    """
    checked = 0
    for chunk in chunks:
        want = chunk.receipt.byte_count
        if want is None:
            continue
        checked += 1
        got = sum(post_body_bytes(row) for row in chunk.rows)
        if got != want:
            return {
                "status": "contradicted",
                "checked": checked,
                "chunks": len(chunks),
                "first_contradiction": {
                    "host_epoch": chunk.receipt.host_epoch,
                    "records": chunk.receipt.records,
                    "byte_count": want,
                    "rows_bytes": got,
                    "first_seq": chunk.rows[0].seq if chunk.rows else None,
                    "last_seq": chunk.rows[-1].seq if chunk.rows else None,
                },
            }
    return {
        "status": "verified" if checked else "unverified",
        "checked": checked,
        "chunks": len(chunks),
        "first_contradiction": None,
    }


def pair_rows(rows: list[Row], receipts: list[Receipt]) -> tuple[list[Pair], int]:
    """Pair each delivered row with the receipt that delivered it."""
    chunks, unpaired = pair_chunks(rows, receipts)
    pairs = [Pair(chunk.receipt.host_epoch, row.seq, row.device_epoch, row.body)
             for chunk in chunks for row in chunk.rows]
    return pairs, unpaired


@dataclass(frozen=True, slots=True)
class RowAge:
    """One delivered row's age, taken from that row's own receipt.

    Three different ages, and the difference between them is the lane's
    backlog:

    - ``delivery_age_s`` - how long the host has held the row.  Exact on the
      host clock, and the lower bound of the observation's own age: the
      sample happened no later than the delivery.
    - ``observed_s`` - the row's own lag, ``host receipt - device epoch``:
      the phone's clock error plus the transport delay, which the lane cannot
      separate.
    - ``sample_age_s`` - the sample's age in the phone's own clock frame.
      Between two rows of one session this difference is the true age
      difference (the same clock, so its offset cancels), and it is the
      frame a consumer must use to compare two observations with each other.

    The lane does **not** bound the observation's absolute age from above: the
    phone's clock may be arbitrarily far ahead of the host, and that is the
    same unresolved side as the clock bound's.
    """
    seq: int
    kind: str
    device_epoch: int
    host_epoch: float
    now: float

    @property
    def delivery_age_s(self) -> float:
        """How long the host has held this row: exact on the host clock."""
        return self.now - self.host_epoch

    @property
    def observed_s(self) -> float:
        """The row's own lag: the phone's clock error plus the transport delay."""
        return self.host_epoch - self.device_epoch

    @property
    def sample_age_s(self) -> float:
        """The sample's age in the phone's own clock frame."""
        return self.now - self.device_epoch


def row_ages(chunks: list[Chunk], now: float) -> list[RowAge]:
    """Every delivered row's age, from that row's own receipt.

    A consumer that wants to know whether *this* observation was live must not
    re-derive the pairing - walking backward from the live tail, because every
    receipt states how many rows its POST appended - and get it wrong the way
    two implementations do.  This is that pairing, published per row.
    """
    ages: list[RowAge] = []
    for chunk in chunks:
        for row in chunk.rows:
            ages.append(RowAge(row.seq, row.kind, row.device_epoch,
                               chunk.receipt.host_epoch, now))
    return ages


def age_index(chunks: list[Chunk], now: float) -> list[dict]:
    """The per-row age table in its compressed form: one entry per POST.

    Every row a POST delivered shares that POST's delivery age, so the index
    carries the age once per POST and names the sequence range it covers.  A
    consumer looks a row up by its sequence number and reads its age here,
    instead of pairing the queue with the journal itself.
    """
    index: list[dict] = []
    for chunk in chunks:
        if not chunk.rows:
            continue
        epochs = [row.device_epoch for row in chunk.rows]
        observed = [chunk.receipt.host_epoch - epoch for epoch in epochs]
        index.append({
            "receipt_epoch": chunk.receipt.host_epoch,
            "receipt_utc": datetime.fromtimestamp(
                chunk.receipt.host_epoch, timezone.utc).isoformat(),
            "rows": len(chunk.rows),
            "first_seq": min(row.seq for row in chunk.rows),
            "last_seq": max(row.seq for row in chunk.rows),
            "delivery_age_s": now - chunk.receipt.host_epoch,
            "sample_age_min_s": now - max(epochs),
            "sample_age_max_s": now - min(epochs),
            "observed_min_s": min(observed),
            "observed_max_s": max(observed),
        })
    return index


def age_index_lookup(index: list[dict], seq: int) -> dict | None:
    """The index entry that carried a given row, if the index carries it.

    The index is in delivery order and its sequence ranges do not overlap, so
    the entry is found by bisection rather than by a scan of the whole
    history.  A delivery batch is written newest first, so the rows inside
    one entry are in descending order; the entries themselves still follow the
    sequence order, and a bisection that finds nothing falls back to a scan,
    so a lane whose batches are not in sequence order is answered correctly
    rather than silently not at all.
    """
    low, high = 0, len(index) - 1
    while low <= high:
        middle = (low + high) // 2
        entry = index[middle]
        if seq < entry["first_seq"]:
            high = middle - 1
        elif seq > entry["last_seq"]:
            low = middle + 1
        else:
            return entry
    for entry in index:
        if entry["first_seq"] <= seq <= entry["last_seq"]:
            return entry
    return None


def age_profile(chunks: list[Chunk], now: float, unpaired_head_rows: int = 0,
               pairing: Pairing | None = None) -> dict:
    """The delivered history's ages, summarised.

    The two ends of the history answer two different questions: the newest
    row's delivery age is the lane's live lag, and the oldest row's sample
    age says how far back the recovered observations reach.

    The pairing's own state travels with it: a history whose pairing was
    repaired is not the same reading as a history whose pairing was clean,
    and the ages above descend from that pairing.
    """
    rows = sum(len(chunk.rows) for chunk in chunks)
    if not chunks:
        return {"status": "unavailable", "reason": "the lane holds no paired rows",
                "rows": 0, "posts": 0, "unpaired_head_rows": unpaired_head_rows}
    receipts = [chunk.receipt.host_epoch for chunk in chunks]
    epochs = [row.device_epoch for chunk in chunks for row in chunk.rows]
    profile = {
        "status": "ok",
        "rows": rows,
        "posts": len(chunks),
        "unpaired_head_rows": unpaired_head_rows,
        "newest_row_delivery_age_s": now - max(receipts),
        "oldest_row_delivery_age_s": now - min(receipts),
        "newest_row_sample_age_s": now - max(epochs),
        "oldest_row_sample_age_s": now - min(epochs),
        "history_span_s": max(epochs) - min(epochs),
    }
    profile["pairing_witness"] = (pairing_witness(pairing) if pairing is not None
                                  else {"status": "unchecked", "checked": 0,
                                        "chunks": len(chunks),
                                        "first_contradiction": None,
                                        "repaired": False, "repairs": [],
                                        "unpaired_rows": unpaired_head_rows})
    return profile


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


def pairing_witness(pairing: Pairing) -> dict:
    """What the byte witness says about this pairing, published.

    A pairing that was corrected, and a pairing that could not be corrected,
    both change which POST a row belongs to - and therefore change every age
    derived from it.  A reader that cannot see the correction cannot tell a
    repaired history from a clean one, so the verdict and every repair travel
    with the reading.

    The verdict is deliberately **not** independent of the repair: the same
    byte witness both corrects a pairing and confirms it.  It says the
    published pairing agrees with the host's own statement of what each POST
    carried; it cannot detect a witness that is wrong for some other reason,
    and it cannot see rows that no receipt owns.  Those rows are counted
    separately, in ``unpaired_rows``.
    """
    verdict = verify_pairing(pairing.chunks)
    return {
        "status": verdict["status"],
        "checked": verdict["checked"],
        "chunks": verdict["chunks"],
        "first_contradiction": verdict["first_contradiction"],
        "repaired": pairing.repaired,
        "repairs": list(pairing.repairs),
        "unpaired_rows": pairing.unpaired_rows,
    }


def measure(rows: list[Row], receipts: list[Receipt]) -> dict:
    """The full delivered-lane reading for one node."""
    pairing = pair_lane(rows, receipts)
    return measure_chunks(pairing.chunks, pairing.unpaired_rows,
                          pairing=pairing)


def measure_chunks(chunks: list[Chunk], unpaired_head_rows: int,
                   pairing: Pairing | None = None) -> dict:
    """The full delivered-lane reading, from an already-paired lane.

    The pairing is the expensive part of reading a delivered history, so it is
    done once and both the bound and the per-row ages are derived from it.

    ``pairing`` carries the repairs the byte witness forced.  When it is
    given, the witness's verdict and every repair are published with the
    reading; when it is not, the reading says so rather than implying a
    pairing nobody checked.
    """
    pairs = [Pair(chunk.receipt.host_epoch, row.seq, row.device_epoch, row.body)
             for chunk in chunks for row in chunk.rows]
    sessions = split_sessions(pairs)
    reading = {
        "paired_rows": len(pairs),
        "unpaired_head_rows": unpaired_head_rows,
        "sessions": [session.summary() for session in sessions],
        "session_objects": sessions,
    }
    if pairing is None:
        reading["pairing_witness"] = {
            "status": "unchecked",
            "checked": 0,
            "chunks": len(chunks),
            "first_contradiction": None,
            "repaired": False,
            "repairs": [],
            "unpaired_rows": unpaired_head_rows,
        }
    else:
        reading["pairing_witness"] = pairing_witness(pairing)
    return reading


def _behind_phrase(bound: float) -> str:
    """The bound said with its sign the right way round.

    A negative bound is not "at most -1 s behind": it says the phone's clock
    was *ahead* of the host's by at least that much.
    """
    if bound < 0:
        return f"at least {abs(bound):.3f}s ahead of the host"
    return f"at most {bound:.3f}s behind the host"


def reason_text(measurement: dict) -> str:
    """A bounded reason naming the route, the bound and the limit.

    The bound is one-sided: it says how far the phone's clock was *behind*
    the host, never how far it may be ahead.  The session-wide figure is the
    one the phone's own monotonic reference makes derivable.

    The reason is the prose a reader of the emitted document gets, so it
    carries the bound and the unresolved side itself: a reader who sees only
    the document must not be able to mistake the bound for a measured offset.
    Its budget is the reader's, not this producer's - a reason past
    :data:`MAX_REASON_CHARS` is replaced by a bare class token, so every
    fact it carried is lost.  The premise and the coverage therefore live in
    the typed field (:func:`clock_bound_field`), which no length limit
    touches, and the clauses that do not fit are dropped rather than
    truncated.
    """
    sessions = measurement["session_objects"]
    if not sessions or not sessions[-1].pairs:
        return "delivered-row lane: no paired rows"
    current = sessions[-1]
    label = (current.session_id or "unknown-session")[:8]
    bound = current.behind_upper_s
    if bound is None:
        # No bound is published: the session-wide bound and the premise-free
        # per-sample bound stand or fall together, because both are withheld
        # by the same reset.  The reason says what the lane does support -
        # the tightest bound its rows give - and the typed field carries the
        # reason the bound is withheld and the coverage the prose cannot
        # hold.
        #
        # The tightest bound is a bound *at the row that produced it*, not a
        # bound that holds across the session, so it says so.  Without that
        # qualifier a reader would take it for a session-wide bound, which is
        # the reading 82349's independent review rejected.
        tightest = min(current.observed) if current.observed else None
        required = [
            f"delivered-row lane: no session-wide bound for session {label}",
            f"the tightest bound is {_behind_phrase(tightest)}, at the row "
            f"that produced it" if tightest is not None else
            "the session's rows support no bound",
            "the ahead side is unbounded by this lane",
        ]
        optional = []
    else:
        covered = len(current.wall_values())
        required = [
            f"delivered-row lane: the phone's clock was {_behind_phrase(bound)} "
            f"at all {covered} monotonic rows of session {label}",
            "the ahead side is unbounded by this lane",
        ]
        optional = []
        latency = current.delivery_latency_lower_s
        if latency is not None:
            optional.append(f"rows were delivered at least {latency:.1f}s late")
    text = "; ".join(required)
    # The required clauses are short enough to fit by construction; the
    # optional ones are dropped rather than truncated when they do not.  A
    # reason past the reader's limit is not shortened by the reader - it is
    # replaced by a bare class token, which loses every fact it carried.
    for clause in optional:
        candidate = f"{text}; {clause}"
        if len(candidate) <= MAX_REASON_CHARS:
            text = candidate
    return text


def clock_bound_field(measurement: dict) -> dict | None:
    """The typed, versioned bound this lane establishes, or ``None``.

    The field carries what prose cannot: the bound as a number with its
    relation stated, the premise it rests on, and the coverage that says
    which rows it holds at.  It is deliberately *not* an offset: the
    delivered lane bounds the skew from one side only, so the field states a
    one-sided relation in the ``phone_minus_host_gte_s`` convention - the
    quantity is the phone's clock minus the host's, and the relation says the
    quantity is at least that many seconds.  ``unbounded_side`` names the
    side the lane does not bound.  A consumer therefore cannot read the
    field as a measured offset, and cannot read it as an interval either.

    The shape is the one core proposed (genome DM 85077): a sibling
    ``clock_bound`` object, version 1, with the signed relation stated as
    ``phone_minus_host_gte_s``, the session it covers, the number of rows
    that carry the monotonic reading, and the premise as a list of the two
    claims it rests on.  The producer agrees that shape; a consumer that
    does not know :data:`CLOCK_BOUND_VERSION` must ignore the field rather
    than guess its shape, which is why the version is inside the field and
    not beside it.

    When the lane supports no bound the field says so and carries the reason
    it is withheld, so a consumer never has to read a paragraph to learn that
    there is no number.  The session-wide bound and the premise-free
    per-sample bound stand or fall together - both are withheld by the same
    reset - so there is no case where one is published and the other is not.
    """
    sessions = measurement["session_objects"]
    if not sessions or not sessions[-1].pairs:
        return None
    current = sessions[-1]
    bound = current.behind_upper_s
    covered = len(current.wall_values())
    if bound is None:
        return {
            "version": CLOCK_BOUND_VERSION,
            "published": False,
            "phone_minus_host_gte_s": None,
            "unbounded_side": "above",
            "withheld_reason": (current.session_wide_withheld_reason
                                or "the lane does not support one"),
            "session_id": current.session_id,
            "covered_rows": covered,
            "rows": len(current.pairs),
            "premise": [],
        }
    latency = current.delivery_latency_lower_s
    field = {
        "version": CLOCK_BOUND_VERSION,
        "published": True,
        # The signed relation: the phone's clock minus the host's is at
        # least this many seconds.  It is a one-sided bound, not an offset.
        "phone_minus_host_gte_s": -bound,
        "unbounded_side": "above",
        "session_id": current.session_id,
        "covered_rows": covered,
        "rows": len(current.pairs),
        "premise": list(BEHIND_BOUND_PREMISES),
    }
    if latency is not None:
        # The lane's lateness, not the phone's clock.  It is what says that a
        # stale timestamp is the delivery path's fault, so it travels with
        # the bound rather than only in prose - the prose budget drops it.
        field["delivery_latency_lower_s"] = latency
    return field


def clock_bound_document(node: str, measurement: dict, now: float) -> dict:
    """The clock-domain document this lane can honestly publish.

    ``status`` is ``unavailable`` and every offset is null: the delivered lane
    bounds the skew from above only, and a document that carried an offset
    would be a reading the lane cannot support.

    The bound itself rides in the typed ``clock_bound`` field, which states
    the relation, the premise and the coverage as values.  The ``reason``
    stays as the prose a reader sees; it is bounded by the reader's own
    limit, so it names the route, the bound and the unresolved side, and
    points at nothing it cannot carry.
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
    bound = clock_bound_field(measurement)
    if bound is not None:
        document["clock_bound"] = bound
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
    witness = measurement.get("pairing_witness") or {}
    if witness.get("status") != "unchecked":
        lines.append(f"  pairing witness: {witness.get('status')} "
                     f"checked={witness.get('checked')} of "
                     f"{witness.get('chunks')} posts; "
                     f"repaired={witness.get('repaired')}; "
                     f"unpaired rows={witness.get('unpaired_rows')}")
        for repair in witness.get("repairs") or []:
            lines.append(f"    repair: {repair.get('resolution')} "
                         f"records={repair.get('records')} "
                         f"byte_count={repair.get('byte_count')} "
                         f"rows_bytes={repair.get('rows_bytes')} "
                         f"first_seq={repair.get('first_seq')} "
                         f"last_seq={repair.get('last_seq')} "
                         f"unpaired_rows={repair.get('unpaired_rows')}")
        contradiction = witness.get("first_contradiction")
        if contradiction:
            lines.append(f"    contradiction: host_epoch="
                         f"{contradiction.get('host_epoch')} "
                         f"records={contradiction.get('records')} "
                         f"byte_count={contradiction.get('byte_count')} "
                         f"rows_bytes={contradiction.get('rows_bytes')}")
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
        lateness = entry.get("lateness") or {}
        if lateness:
            lines.append(
                f"    lateness: rows={lateness['rows']} "
                f"observed spread={lateness['spread_s']:.3f}s; "
                f"stale rows (>{lateness['stale_threshold_s']:.0f}s)="
                f"{lateness['stale_rows']}; "
                f"one constant correction within {lateness['tolerance_s']:.0f}s="
                f"{lateness['constant_correction_within_tolerance']}; "
                f"worst relative-age residual="
                f"{lateness['relative_age_residual_s']:.3f}s")
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


def write_age_index(path: Path, index: list[dict]) -> None:
    """Write the age index atomically, one JSON line per delivered POST.

    The index is large - one line per POST of the whole delivered history -
    so it is written as a stream rather than as one JSON document, and it is
    written atomically for the same reason the clock-domain document is: a
    reader must never see half of one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".age-index-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            for entry in index:
                stream.write(json.dumps(entry, sort_keys=True))
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
    parser.add_argument("--emit-age-index", default=None,
                        help="write the per-row age index here, one JSON line "
                             "per delivered POST")
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
    if arguments.emit_age_index:
        pairing = pair_lane(rows, receipts)
        chunks = pairing.chunks
        unpaired = pairing.unpaired_rows
        now = datetime.now(timezone.utc).timestamp()
        index = age_index(chunks, now)
        path = Path(arguments.emit_age_index)
        write_age_index(path, index)
        print(f"age index written: {arguments.emit_age_index} "
              f"({len(index)} posts, {sum(entry['rows'] for entry in index)} rows, "
              f"{unpaired} unpaired head rows)")
        witness = pairing_witness(pairing)
        print(f"pairing witness: {witness['status']} "
              f"checked={witness['checked']} of {witness['chunks']} posts, "
              f"repaired={witness['repaired']}")
        for repair in witness["repairs"]:
            print(f"  repair: {repair.get('resolution')} "
                  f"records={repair.get('records')} "
                  f"byte_count={repair.get('byte_count')} "
                  f"rows_bytes={repair.get('rows_bytes')} "
                  f"first_seq={repair.get('first_seq')} "
                  f"last_seq={repair.get('last_seq')} "
                  f"unpaired_rows={repair.get('unpaired_rows')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
