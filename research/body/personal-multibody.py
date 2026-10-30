#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Deterministic personal multi-body reading over the Android perception sources.

Purpose
-------
The human's standing objective is an analysis of themselves across all
authorized Mishe bodies and senses.  A narrative is not a reading: it must be
re-derivable from the same bytes on the next wake.  This tool reads the
delivered perception state of every node and prints, per node, what the
evidence supports, what it does not, and which readings are UNKNOWN.

Sources (read-only)
--------------------
``<space>/space.json``            the plant's node view (symlinked to the
                                   Android site's copy in this plant)
``<space>/body/perception/<node>.json``  the delivered, bounded history window
``<space>/body/clock-domain-<node>.json``  the phone/host clock offset document
                                   (absent for a node means the offset is
                                   UNKNOWN, never zero)
``<space>/body/queues/<node>.observations.log``  the delivered observation
                                   history: ``SEQ <phone clock> <kind> ...``
                                   one row per observation, in the order the
                                   body produced them
``<space>/body/queues/writes.jsonl``  the host's accepted-POST journal, with
                                   the host receipt time of every delivery

The tool never writes, never mutates the Android site, and never phones
anything.  It only reads files that already exist.

Gates (stated so a reading can be checked, not believed)
---------------------------------------------------------
* **Current window** - a node contributes to a current reading only when its
  channel delivery is ``pass`` and its rows are newer than ``--max-age``.
* **Fusion** - a cross-body claim needs *every* node to hold a current window;
  otherwise the fused reading is UNKNOWN (a stale body is not a present one).
* **Absolute phone time** - needs a clock-domain document for that node; the
  host receipt anchors a reading, the phone clock cannot.  The delivered lane
  cannot substitute for it: see *clock identifiability* below.
* **Motion labels** - a gait/activity label needs a sampling rate that can
  resolve gait.  At 5 s per sample the gait band (roughly 1-2 Hz) is aliased
  away, so no activity label is supportable however stable the magnitude is.
* **Stillness** - the composite stillness verdict is not a presence or
  occupancy claim (the Android consumer says so itself) and it is UNKNOWN
  whenever one of its three inputs is unusable.
* **An unavailable channel is never a quiet channel** - rows that carry a
  reason instead of a value are reported as unavailable, with the reason.
* **A hole is attributed, not assumed** - the delivered queue says when the
  *body* produced nothing, the host journal says when the *link* delivered
  nothing, and a silence in both at once is reported as not separable.
* **Clock identifiability** - the phone's clock offset is not recoverable from
  the delivered rows; a constant transport delay and a constant clock offset
  produce identical bytes.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: A gait band of roughly 1-2 Hz needs at least this many samples per second
#: before a motion label can be read from acceleration.
GAIT_RESOLUTION_HZ = 4.0

#: A silence shorter than this is not a hole.  It is a parameter because the
#: body that owns the queue sets its own sampling cadence; the default suits
#: the 5 s cadence of the carried body.
DEFAULT_HOLE_THRESHOLD_S = 300.0

#: How many malformed lines are carried into the report as evidence.  A
#: malformed line is preserved, not silently dropped.
MALFORMED_SAMPLES_KEPT = 3

#: A delivery outage the body kept producing through, as a fraction of the
#: samples its cadence says the gap held.  A gap that shows this much of its
#: backlog is a *control*: it shows what a delivery outage leaves behind in
#: this lane, against the same device, app, server and network.
BACKLOG_CONTROL_RATIO = 0.5

#: How far below the lane's own weakest demonstrated outage a gap's backlog
#: has to be before the reading calls the gap body-side.  Twenty times is two
#: orders of magnitude: an outage withholds the production it interrupted, so
#: its backlog is in proportion to that production.
BACKLOG_CONTROL_FACTOR = 20.0

#: The premise the backlog test rests on.  It counts the rows whose *device*
#: clock falls inside a host-clock gap, so a device wall clock that stepped
#: forward across the gap could hide production the test would not see.
BACKLOG_TEST_PREMISE = (
    "the backlog test counts delivered rows by the device's own clock, so it "
    "assumes the device's wall clock did not step forward across the "
    "interval; the device's monotonic counter shows that the clock did not "
    "step inside either session, and a step that survived into the delivered "
    "rows shows in the lane's own clock bound, but a step made during the "
    "silence itself is not visible to this test")

#: The clock of the carried body cannot be recovered from the delivered lane.
#: This is stated for every node, whatever else the reading holds.
_CLOCK_NOT_IDENTIFIABLE = (
    "the phone clock offset is not recoverable from the delivered lane: a "
    "constant transport delay and a constant clock offset produce identical "
    "rows, so the offset stays one-sided; what the lane does measure is the "
    "phone's clock movement against its own monotonic clock and the lane's "
    "own lateness, and those are reported beside the bound")

#: The sibling instrument that measures what the delivered lane can say about
#: a phone's clock.  It is loaded from this file's own directory, so the
#: reading does not depend on how the tool was invoked.
_CLOCK_BOUND_PATH = Path(__file__).resolve().parent / "clock_bound.py"

#: The loaded sibling instrument, once.
_CLOCK_BOUND_MODULE = None


def _clock_bound_module():
    """Load the sibling clock-bound instrument, once.

    The reading computes the delivered lane's bound itself rather than
    parsing another plant's document: the bound is a measurement of the
    delivered bytes, and a document that carried it would be a second thing
    to keep in step with the lane.
    """
    global _CLOCK_BOUND_MODULE
    if _CLOCK_BOUND_MODULE is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("clock_bound",
                                                      _CLOCK_BOUND_PATH)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load the clock-bound instrument at "
                              f"{_CLOCK_BOUND_PATH}")
        module = importlib.util.module_from_spec(spec)
        # The dataclasses in the sibling look their own module up in
        # ``sys.modules`` while they are being built, so it has to be there
        # before the module runs.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _CLOCK_BOUND_MODULE = module
    return _CLOCK_BOUND_MODULE


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _body_dir(space: Path) -> Path:
    """The directory holding ``space.json``.

    The Android site keeps it at ``<site>/body/space.json``; the plant's own
    copy is a symlink to that file.  Both layouts are accepted, and the
    directory that actually holds the file is the one used for the rest.
    """
    for candidate in (space / "body", space):
        if (candidate / "space.json").exists():
            return candidate
    return space / "body"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _row_fields(row: dict) -> dict:
    fields = row.get("fields")
    return fields if isinstance(fields, dict) else {}


def _number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _acceleration_summary(rows: list[dict]) -> dict:
    """Magnitude statistics over the retained acceleration rows."""
    magnitudes: list[float] = []
    samples: list[float] = []
    for row in rows:
        fields = _row_fields(row)
        x = _number(fields.get("x"))
        y = _number(fields.get("y"))
        z = _number(fields.get("z"))
        if None in (x, y, z):
            continue
        magnitudes.append(math.sqrt(x * x + y * y + z * z))
        collected = row.get("collected")
        if isinstance(collected, (int, float)):
            samples.append(float(collected))
    summary = {"count": len(magnitudes)}
    if magnitudes:
        summary.update({
            "magnitude_min": round(min(magnitudes), 4),
            "magnitude_max": round(max(magnitudes), 4),
            "magnitude_mean": round(statistics.fmean(magnitudes), 4),
            "magnitude_std": round(statistics.pstdev(magnitudes), 4),
        })
    if len(samples) >= 2:
        span = max(samples) - min(samples)
        summary["window_span_s"] = round(span, 3)
        summary["sample_rate_hz"] = round((len(samples) - 1) / span, 4) if span > 0 else None
    return summary


def _light_summary(rows: list[dict]) -> dict:
    values: list[float] = []
    for row in rows:
        lux = _number(_row_fields(row).get("lux"))
        if lux is not None:
            values.append(lux)
    summary = {"count": len(values)}
    if values:
        summary.update({
            "lux_min": min(values),
            "lux_max": max(values),
            "lux_mean": round(statistics.fmean(values), 4),
            "lux_std": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
        })
    return summary


def _battery_summary(rows: list[dict]) -> dict:
    levels = [_number(_row_fields(row).get("level")) for row in rows]
    levels = [value for value in levels if value is not None]
    charging = sorted({str(_row_fields(row).get("charging")) for row in rows})
    summary = {"count": len(levels)}
    if levels:
        summary.update({"level_min": min(levels), "level_max": max(levels)})
    summary["charging_states"] = charging
    return summary


def _other_summary(rows: list[dict]) -> dict:
    reasons = sorted({str(_row_fields(row).get("reason")) for row in rows})
    return {"count": len(rows), "reasons": reasons}


#: The field that carries a channel's measurement.  A row without it is a
#: channel that reported *why* it has no value, not a channel that is quiet.
CHANNEL_VALUE_FIELD = {
    "acceleration": "z",
    "light": "lux",
    "battery": "level",
    "wifi_link_rssi_dbm": "wifi_link_rssi_dbm",
}


def _channel_availability(kind: str, rows: list[dict]) -> dict:
    """Whether a channel carries values, or only a reason for having none.

    A channel whose rows all carry ``reason`` (the accelerometer reporting
    ``stale``, the wifi API reporting ``disconnected``) is *unavailable*, and
    an unavailable channel is never evidence of a quiet one.
    """
    field = CHANNEL_VALUE_FIELD.get(kind)
    valued = 0
    reasons: set[str] = set()
    for row in rows:
        fields = _row_fields(row)
        if field is not None and fields.get(field) is not None:
            valued += 1
        reason = fields.get("reason")
        if reason is not None:
            reasons.add(str(reason))
    if valued:
        status = "valued"
    elif reasons:
        status = "unavailable"
    else:
        status = "no-value"
    return {"rows": len(rows), "valued_rows": valued, "status": status,
            "reasons": sorted(reasons) or None}


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside brackets or quotes."""
    parts: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            continue
        if char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return parts


def _channel_value(text: str, channel: str) -> str | None:
    """The state a state line carries for one channel, or ``None``.

    State lines spell a channel either as ``name=[freshness, value, reason]``
    or as ``name=value``.  Both forms are reduced to one comparable string
    that keeps the freshness and the reason, so that "no value because the
    sensor is stale" never reads like "no value at all".
    """
    marker = channel + "="
    start = text.find(marker)
    if start < 0:
        return None
    rest = text[start + len(marker):]
    if not rest:
        return None
    if rest[0] != "[":
        return f"value={rest.split()[0]}"
    depth = 0
    quote = ""
    for index, char in enumerate(rest):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                parts = _split_top_level(rest[1:index])
                parts += [""] * (3 - len(parts))
                return (f"freshness={parts[0]} value={parts[1]} "
                        f"reason={parts[2]}")
    return None


def _event_transitions(body: Path, node: str, limit: int = 8) -> dict:
    """Dated channel-state changes for one node, from the site's event log.

    The event log is a secondary source: a missing or unreadable log yields
    ``status: unavailable`` and never a claim.  Each entry names the channel,
    the instant the site recorded it, and the before/after value.
    """
    path = body / "perception" / "events.txt"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"status": "unavailable", "reason": "events.txt unreadable",
                "transitions": []}

    transitions: list[dict] = []
    previous: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("measured-light-bucket-transition"):
            if f"id={node}:" not in stripped:
                continue
            bucket = re.search(r"bucket=([^\s;]+)->([^\s;]+)", stripped)
            lux = re.findall(r"lux=([\d.]+)", stripped)
            sample = re.search(r"to=#\d+ lux=[\d.]+ phone_sample=(\S+)", stripped)
            transitions.append({
                "kind": "light-bucket",
                "at": sample.group(1) if sample else None,
                "from": lux[0] if len(lux) > 0 else None,
                "to": lux[1] if len(lux) > 1 else None,
                "bucket": f"{bucket.group(1)}->{bucket.group(2)}" if bucket else None,
            })
            continue
        if not stripped.startswith("20"):
            continue
        fields = stripped.split()
        if len(fields) < 2 or fields[1] != node:
            continue
        at = fields[0]
        for channel in CHANNEL_VALUE_FIELD:
            value = _channel_value(stripped, channel)
            if value is None:
                continue
            if previous.get(channel) != value:
                transitions.append({
                    "kind": "channel-state",
                    "at": at,
                    "channel": channel,
                    "from": previous.get(channel),
                    "to": value,
                })
                previous[channel] = value
    if not transitions:
        return {"status": "unavailable", "reason": "no transitions for this node",
                "transitions": []}
    return {"status": "ok", "source": str(path), "transitions": transitions[-limit:]}


def _row_span(rows: list[dict]) -> dict:
    collected = [row.get("collected") for row in rows
                 if isinstance(row.get("collected"), (int, float))]
    received = [row.get("received") for row in rows
                if isinstance(row.get("received"), (int, float))]
    return {
        "rows": len(rows),
        "phone_sample_first_utc": _iso(min(collected)) if collected else None,
        "phone_sample_last_utc": _iso(max(collected)) if collected else None,
        "host_receipt_first_utc": _iso(min(received)) if received else None,
        "host_receipt_last_utc": _iso(max(received)) if received else None,
    }


def _delivery_lag(rows: list[dict]) -> dict | None:
    """The observed gap between the phone's sample clock and the host receipt.

    This is a *lag*, not a clock offset: it carries the transport delay as
    well as the phone's clock error, and the two cannot be separated here.
    """
    lags: list[float] = []
    for row in rows:
        collected = row.get("collected")
        received = row.get("received")
        if isinstance(collected, (int, float)) and isinstance(received, (int, float)):
            lags.append(float(received) - float(collected))
    if not lags:
        return None
    return {
        "samples": len(lags),
        "min_s": round(min(lags), 3),
        "median_s": round(statistics.median(lags), 3),
        "max_s": round(max(lags), 3),
    }


# --- delivered observation history ---------------------------------------

#: A queue row is ``SEQ <phone clock> <kind> <fields...>``.
_QUEUE_ROW = re.compile(r"^(\d+)\s+(\d+)\s+(\S+)(.*)$")

#: The app's own record that it destroyed rows its bounded spool could not
#: hold.  The app writes the record as ``<epoch> spool overflow dropped=N
#: policy=oldest`` and the host prefixes the queue's sequence number, so the
#: delivered row reads ``<seq> <epoch> spool overflow dropped=N
#: policy=oldest``.  The phrase is matched over the whole row rather than
#: over one field, because the row's *kind* is the word ``spool``: matching
#: only the trailing fields would never fire on a real row and would make
#: the absence of the record look like a measurement.
_SPOOL_OVERFLOW = re.compile(r"\bspool\s+overflow\s+dropped=(\d+)\b")


def _read_queue(path: Path) -> dict:
    """Read the delivered observation queue into ordered samples.

    Malformed lines are counted and kept as evidence: a queue that ends in
    unreadable rows is a fact about the delivery, not noise to be dropped.
    """
    rows: list[tuple[int, int, str]] = []
    malformed: list[str] = []
    malformed_rows = 0
    spool_overflow_rows = 0
    spool_overflow_notices: list[dict] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError as error:
        return {"status": "unavailable", "reason": f"queue unreadable: {error}",
                "path": str(path)}
    with handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            match = _QUEUE_ROW.match(stripped)
            if match is None:
                malformed_rows += 1
                if len(malformed) < MALFORMED_SAMPLES_KEPT:
                    malformed.append(stripped[:160])
                continue
            overflow = _SPOOL_OVERFLOW.search(stripped)
            if overflow is not None:
                # The app's own record that it produced rows it could not
                # deliver.  It is the one place in the delivered bytes where
                # production that never reached the host would show.  The
                # notice carries the epoch at which the app dropped the rows
                # and the cumulative number of rows it has dropped, so it is
                # evidence about an interval rather than about the lane.
                spool_overflow_rows += 1
                spool_overflow_notices.append({
                    "epoch": int(match.group(2)),
                    "dropped": int(overflow.group(1)),
                })
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3)))
    return {
        "status": "ok",
        "path": str(path),
        "rows": rows,
        "malformed_rows": malformed_rows,
        "malformed_samples": malformed,
        "spool_overflow_rows": spool_overflow_rows,
        "spool_overflow_notices": spool_overflow_notices,
    }


def _cadence_channel(rows: list[tuple[int, int, str]]) -> tuple[str, list[int]]:
    """The channel whose samples define the body's cadence, and its clock series.

    The busiest channel is used, with the name as a deterministic tie-break.
    Its clock readings are collapsed into sample points: consecutive rows that
    share one clock reading are one sample of several channels, not several
    samples.
    """
    counts: dict[str, int] = {}
    for _, _, kind in rows:
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return "", []
    channel = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    series: list[int] = []
    for _, epoch, kind in rows:
        if kind != channel:
            continue
        if not series or series[-1] != epoch:
            series.append(epoch)
    return channel, series


def _host_posts(path: Path, node: str) -> list[float]:
    """The host's receipt times for this node, from the accepted-POST journal."""
    times: list[float] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return times
    with handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("node") != node or entry.get("kind") != "observations":
                continue
            stamp = entry.get("ts")
            if not isinstance(stamp, str):
                continue
            try:
                times.append(datetime.fromisoformat(stamp).timestamp())
            except ValueError:
                continue
    times.sort()
    return times


def _clock_steps(series: list[int]) -> dict:
    """How often the body's own clock stepped backwards, and by how much.

    A clock that steps backwards is a clock whose readings are not a
    timeline, and its step size bounds how far two clocks can silently
    disagree.
    """
    backward = [series[index] - series[index + 1]
                for index in range(len(series) - 1)
                if series[index + 1] < series[index]]
    return {
        "backward_count": len(backward),
        "backward_total_s": sum(backward),
        "max_backward_s": max(backward) if backward else 0,
    }


def _missing_time(series: list[int], cadence: float) -> float:
    """The time the body was not producing observations, beyond one step.

    Every forward jump longer than one cadence step is missing time, however
    short it is; a hole is the same measurement restricted to the intervals
    that exceed the threshold.
    """
    total = 0.0
    for index in range(len(series) - 1):
        delta = series[index + 1] - series[index]
        if delta > cadence:
            total += delta - cadence
    return total


def _holes(series: list[int], cadence: float, threshold: float) -> list[tuple[int, int]]:
    """Intervals in which the body produced nothing for longer than the threshold.

    A hole is a forward jump *beyond one expected cadence step*, so an ordinary
    sampling interval is never a hole however the threshold is set, and the
    measured silence is the part of the interval that was actually missing.
    """
    holes: list[tuple[int, int]] = []
    for index in range(len(series) - 1):
        start = series[index]
        end = series[index + 1]
        if end - start - cadence > threshold:
            holes.append((start, end))
    return holes


def _aligned_offset(clock: dict) -> tuple[float | None, float]:
    """The phone-to-host clock offset and the uncertainty in it.

    The clock document's convention is ``phone clock - host clock``, so a
    phone reading ``p`` is the host instant ``p - offset``.  Where no offset is
    measured there is no supported alignment at all, and the uncertainty is
    the body's own observed clock instability - a lower bound, because the
    offset itself is unknown and unbounded.
    """
    if clock.get("status") != "ok":
        return None, 0.0
    nominal = clock.get("offset_nominal_s")
    if nominal is None:
        return None, 0.0
    low = clock.get("offset_min_s")
    high = clock.get("offset_max_s")
    spread = 0.0
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        spread = max(abs(float(nominal) - float(low)), abs(float(nominal) - float(high)))
    return float(nominal), spread


def _overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


#: What the app's own overflow notice can and cannot establish.  The notice
#: is the app's record that it produced rows it could not deliver, so it is
#: the one place in the delivered bytes where production that never reached
#: the host would show.  The notice reaches the host only if the app uploads
#: after dropping, so its *absence* does not prove the app dropped nothing:
#: a drop the app never managed to upload leaves no trace here.  The notice
#: carries the epoch at which the app dropped the rows, so it is evidence
#: about an interval rather than about the lane.
_SPOOL_EVIDENCE = (
    "the app's own spool-overflow record is the one place in the delivered "
    "bytes where production that never reached the host would show, and it "
    "carries the epoch at which the app dropped the rows; the notice reaches "
    "the host only if the app uploads after dropping, so its absence does "
    "not establish that the app dropped nothing")


def _spool_drops_in(notices: list[dict], start: float, end: float) -> list[dict]:
    """The app's own drop notices dated inside an interval.

    The notice's epoch is the phone's wall clock at the moment of the drop,
    so it is compared against the interval in the same clock the hole is
    measured in.  A notice inside the interval is the app saying it produced
    rows it could not deliver *then*, which is exactly the alternative a
    body-side reading has to exclude.
    """
    inside = []
    for notice in notices:
        epoch = float(notice["epoch"])
        if start <= epoch <= end:
            inside.append(notice)
    return inside


def _spool_drops_in_host(notices: list[dict], start: float, end: float,
                         to_host, uncertainty: float) -> list[dict]:
    """The app's own drop notices dated inside a *host*-clock interval.

    A delivery outage is measured on the host's clock and a drop notice
    carries the phone's, so the notice is carried onto the host's timeline
    before it is compared.  The comparison is only as good as the two clocks'
    relation, so it is widened by the same uncertainty the rest of the
    reading uses for that relation.
    """
    inside = []
    for notice in notices:
        host = to_host(float(notice["epoch"]))
        if start - uncertainty <= host <= end + uncertainty:
            inside.append(notice)
    return inside


def _backlog_control(link_holes: list[dict]) -> dict:
    """What a delivery outage leaves behind in this lane, measured from itself.

    A link outage withholds the rows the body produced and delivers them
    afterwards, so the gap's window holds that backlog.  The lane's own other
    outages are therefore the control: they show what a delivery outage looks
    like *here*, against the same device, app, server and network.  A gap that
    leaves almost no backlog while every other gap leaves a full one is a gap
    in which the body produced nothing - there was no production for the link
    to withhold.

    Without such a control the lane offers nothing to compare against, and
    the reading keeps saying that the two causes are not separable.
    """
    controls = [hole for hole in link_holes
                if hole.get("backlog_ratio") is not None
                and hole["backlog_ratio"] >= BACKLOG_CONTROL_RATIO]
    if not controls:
        return {"status": "unavailable",
                "reason": "no other gap in this lane shows a delivery outage "
                          "that withheld production, so the lane holds no "
                          "control for what an outage leaves behind",
                "gaps": 0}
    ratios = [hole["backlog_ratio"] for hole in controls]
    weakest = min(controls, key=lambda hole: hole["backlog_ratio"])
    return {
        "status": "ok",
        "gaps": len(controls),
        "min_ratio": round(min(ratios), 6),
        "median_ratio": round(statistics.median(ratios), 6),
        "weakest": {
            "start_utc": weakest["start_utc"],
            "end_utc": weakest["end_utc"],
            "backlog_ratio": weakest["backlog_ratio"],
            "samples_inside": weakest["samples_inside"],
            "expected_samples": weakest["expected_samples"],
        },
        "factor": BACKLOG_CONTROL_FACTOR,
        "premise": BACKLOG_TEST_PREMISE,
    }


def _continuity(body: Path, node: str, threshold: float, clock: dict,
                lane: dict | None = None) -> dict:
    """The body's observation continuity over the delivered history.

    Two silences are measured and kept apart:

    * a *body hole* - the body produced no observations across the interval,
      measured on the body's own clock in the delivered queue;
    * a *link hole* - the host received nothing across the interval, measured
      on the host's clock in the accepted-POST journal.

    The two are on different clocks, so they are only compared after an
    alignment.  Where the phone/host offset is measured, the phone's readings
    are converted to host instants and the comparison is exact within the
    measured bounds.  Where it is not, the comparison is widened by the
    body's own observed clock instability and every verdict names the clock
    difference that would overturn it.

    A link hole during which the body kept producing observations at its
    cadence is a delivery outage whose observations were recovered afterwards.
    A hole in both at once is reported as not separable: the delivered lane
    cannot say whether the body stopped or the link stopped.

    One case *is* separable, and the lane separates it by its own evidence.
    When the lane's other gaps show that a delivery outage here withholds a
    backlog, a gap that leaves almost no backlog is a gap in which the body
    produced nothing: there was no production for the link to withhold.  The
    device's own monotonic counter is read beside that verdict, because a
    counter that fell across the gap is the device saying it restarted.
    """
    queue = _read_queue(body / "queues" / f"{node}.observations.log")
    if queue.get("status") != "ok":
        return {"status": "unavailable",
                "reason": queue.get("reason", "queue unavailable"),
                "queue": {key: queue[key] for key in
                          ("path", "malformed_rows", "malformed_samples")
                          if key in queue}}
    rows: list[tuple[int, int, str]] = queue["rows"]
    if not rows:
        return {"status": "unavailable",
                "reason": "queue holds no readable rows",
                "queue": {"path": queue["path"],
                          "malformed_rows": queue["malformed_rows"],
                          "malformed_samples": queue["malformed_samples"]}}

    channel, series = _cadence_channel(rows)
    if len(series) < 2:
        return {"status": "unavailable",
                "reason": "queue holds too few samples to measure continuity",
                "queue": {"path": queue["path"],
                          "malformed_rows": queue["malformed_rows"],
                          "malformed_samples": queue["malformed_samples"]}}

    sequences = [row[0] for row in rows]
    contiguous = all(sequences[index + 1] == sequences[index] + 1
                     for index in range(len(sequences) - 1))
    if not contiguous:
        return {"status": "unavailable",
                "reason": "the delivered queue's sequence is not contiguous, so "
                          "rows are missing and continuity cannot be inferred",
                "queue": {"path": queue["path"],
                          "rows": len(rows),
                          "malformed_rows": queue["malformed_rows"],
                          "malformed_samples": queue["malformed_samples"]}}

    steps = _clock_steps(series)
    deltas = [series[index + 1] - series[index] for index in range(len(series) - 1)]
    positive = [delta for delta in deltas if delta > 0]
    cadence = statistics.median(positive) if positive else None
    if not cadence:
        return {"status": "unavailable",
                "reason": "the delivered queue holds no forward samples, so no "
                          "cadence can be measured"}

    posts = _host_posts(body / "queues" / "writes.jsonl", node)
    span_start = float(series[0])
    span_end = float(series[-1])
    span_s = span_end - span_start

    offset, offset_spread = _aligned_offset(clock)
    aligned = offset is not None
    # The uncertainty of any comparison between the two clocks: the measured
    # offset's own bounds where it is measured, and the body's observed clock
    # instability where it is not.
    uncertainty = offset_spread + steps["max_backward_s"] if aligned else steps["max_backward_s"]

    # The intervals in which the host received nothing, on the host's clock.
    link_silence: list[tuple[float, float]] = []
    for index in range(len(posts) - 1):
        start = float(posts[index])
        end = float(posts[index + 1])
        if end - start > threshold:
            link_silence.append((start, end))
    host_span = (float(posts[0]), float(posts[-1])) if len(posts) >= 2 else None

    def to_host(moment: float) -> float:
        """A phone reading as the host instant it corresponds to."""
        return moment - offset if aligned else moment

    def link_silent_share(start: float, end: float) -> tuple[float | None, float]:
        """The fraction of a phone-clock interval during which the host was silent.

        ``None`` when the host journal cannot say: fewer than two posts define
        no interval, and an interval outside the journal's observed span was
        never observed.  The second value is the clock difference that would
        overturn the answer.
        """
        if host_span is None:
            return None, 0.0
        host_start = to_host(start)
        host_end = to_host(end)
        if host_start < host_span[0] - uncertainty or host_end > host_span[1] + uncertainty:
            return None, 0.0
        covered = 0.0
        for hole_start, hole_end in link_silence:
            covered += _overlap(host_start, host_end,
                                hole_start - uncertainty, hole_end + uncertainty)
        share = min(1.0, covered / (host_end - host_start)) if host_end > host_start else 0.0
        if share >= 1.0 - 1e-9:
            share = 1.0
        # How far the two clocks could differ before this answer changes.  A
        # shift of the body's timeline moves the interval; the answer changes
        # when the shift reaches the nearest edge of the nearest silence.
        margins: list[float] = []
        for silence_start, silence_end in link_silence:
            low = silence_start - uncertainty
            high = silence_end + uncertainty
            if high <= host_start:
                margins.append(host_start - high)
            elif low >= host_end:
                margins.append(low - host_end)
            else:
                margins.append(min(high - host_start, host_end - low))
        if not margins:
            margins.append(min((host_span[1] + uncertainty) - host_start,
                               host_end - (host_span[0] - uncertainty)))
        margin = max(0.0, min(margins))
        return share, margin

    sorted_series = sorted(series)

    def samples_inside(start: float, end: float) -> int:
        """The cadence-channel samples inside a host-clock interval.

        Counting is order-independent, so the sorted copy answers it in
        logarithmic time however many holes the history holds.
        """
        low = start - uncertainty
        high = end + uncertainty
        return (bisect.bisect_right(sorted_series, high)
                - bisect.bisect_left(sorted_series, low))

    first_row_at: dict[int, str] = {}
    for _, epoch, kind in rows:
        first_row_at.setdefault(epoch, kind)

    spool_notices = queue.get("spool_overflow_notices") or []

    link_holes: list[dict] = []
    link_windows: list[tuple[float, float, dict]] = []
    for index in range(len(posts) - 1):
        start = float(posts[index])
        end = float(posts[index + 1])
        if end - start <= threshold:
            continue
        produced = samples_inside(start, end)
        expected = (end - start) / cadence
        hole = {
            "start_utc": _iso(start),
            "end_utc": _iso(end),
            "duration_s": round(end - start, 3),
            "samples_inside": produced,
            "expected_samples": round(expected),
            "rows_sampled_inside": sum(1 for _, epoch, _ in rows
                                       if start <= epoch <= end),
            "backlog_ratio": (round(produced / expected, 6) if expected > 0
                              else None),
            "attribution": None,
            "cause": None,
            "_produced": produced,
            "_expected": expected,
        }
        link_holes.append(hole)
        link_windows.append((start, end, hole))

    # The lane's own other gaps are the control for what a delivery outage
    # leaves behind here, so the verdicts below can name a body-side pause
    # instead of reporting every silence in both clocks as inseparable.
    control = _backlog_control(link_holes)

    for hole_start, hole_end, hole in link_windows:
        produced = hole.pop("_produced")
        expected = hole.pop("_expected")
        ratio = hole["backlog_ratio"]
        tolerance = max(2.0, 0.05 * expected)
        # The app's own drop notices dated inside this interval, on the
        # host's timeline.  The same evidence has to reach both verdicts of
        # one interval: a reading that says "body-side" here and "not
        # separable" there for the same interval is not a reading.
        drops = _spool_drops_in_host(spool_notices, hole_start, hole_end,
                                     to_host, uncertainty)
        hole["spool_drops_inside"] = drops
        if produced >= expected - tolerance:
            hole["attribution"] = "link-side"
            hole["cause"] = ("link-side: the body kept producing observations at "
                             f"its cadence ({produced} samples, about "
                             f"{round(expected)} expected) and they were "
                             "delivered afterwards")
        elif (control["status"] == "ok" and ratio is not None
              and ratio < control["min_ratio"] / BACKLOG_CONTROL_FACTOR):
            if drops:
                # The app's own record contradicts the body-side reading: the
                # app was holding production it could not deliver at a moment
                # inside this interval.  A verdict cannot stand on evidence
                # that refutes it.
                hole["attribution"] = "not-separable"
                hole["cause"] = (
                    "not separable: the body produced almost nothing across "
                    f"this interval ({produced} samples where about "
                    f"{round(expected)} were expected at its cadence) while "
                    "every other delivery outage in this lane left a backlog "
                    f"of at least {round(control['min_ratio'] * 100, 1)}% of "
                    "its expected samples, but the app's own spool-overflow "
                    "notice is dated inside this interval, so the app did hold "
                    "production it could not deliver then and the delivered "
                    "lane cannot separate a body-side pause from production "
                    "the app dropped")
            else:
                hole["attribution"] = "body-side"
                hole["cause"] = (
                    "body-side: the body produced almost nothing across this "
                    f"interval ({produced} samples where about {round(expected)} "
                    "were expected at its cadence), while every other delivery "
                    "outage in this lane left a backlog of at least "
                    f"{round(control['min_ratio'] * 100, 1)}% of its expected "
                    "samples, so the link withheld no production because there "
                    "was none to withhold")
        elif produced > 0:
            hole["attribution"] = "partial"
            hole["cause"] = ("partial: the body produced only "
                             f"{produced} samples where about {round(expected)} "
                             "were expected at its cadence, so the body faltered "
                             "as well as the link")
        else:
            hole["attribution"] = "not-separable"
            hole["cause"] = ("not separable: the host received nothing and the "
                             "body produced nothing across the same interval")

    def covering_link_hole(start: float, end: float) -> dict | None:
        """The link hole that covers a body hole, where one does.

        The two are the same interval seen on two clocks, so they are matched
        by overlap: the body hole's own window, widened by the clock
        uncertainty, against the link holes' host-clock windows.
        """
        low = to_host(start) - uncertainty
        high = to_host(end) + uncertainty
        covering = None
        widest = 0.0
        for hole_start, hole_end, hole in link_windows:
            overlap = min(hole_end, high) - max(hole_start, low)
            if overlap > widest:
                widest = overlap
                covering = hole
        return covering

    boots = _device_boots(lane)

    def reboot_inside(start: float, end: float) -> dict | None:
        """The device's reboot inside an interval, where one happened."""
        for boot in boots.get("reboots") or []:
            epoch = boot.get("boot_estimate_epoch_s")
            if epoch is not None and start <= epoch <= end:
                return boot
        return None

    body_holes: list[dict] = []
    for start, end in _holes(series, cadence, threshold):
        resumption_kind = first_row_at.get(end)
        share, margin = link_silent_share(start, end)
        expected = (end - start) / cadence
        covering = covering_link_hole(start, end)
        reboot = reboot_inside(start, end)
        # The app's own drop notices dated inside this interval.  They are
        # measured in the phone's clock, which is the clock the hole is
        # measured in.
        drops_here = _spool_drops_in(spool_notices, start, end)
        if drops_here:
            # The app's own record contradicts a body-side reading: the app
            # was holding production it could not deliver at a moment inside
            # this interval.  A verdict cannot stand on evidence that refutes
            # it, so the reading keeps the interval open.  This is checked
            # before the host journal's silence, because the notice is
            # evidence of its own: a spool that overflowed is what an
            # undeliverable link looks like from the device's side.
            attribution = "not-separable"
            cause = ("not separable: the body produced no observations across "
                     "this interval of its own clock, but the app's own "
                     "spool-overflow notice is dated inside this interval, so "
                     "the app did hold production it could not deliver then "
                     "and the delivered lane cannot separate a body-side pause "
                     "from production the app dropped")
        elif share is None:
            attribution = "unknown"
            cause = ("the body produced no observations across this interval of "
                     "its own clock; the host journal cannot say whether it was "
                     "receiving then, so whether the link was live is UNKNOWN")
        elif covering is not None and covering.get("attribution") == "body-side":
            attribution = "body-side"
            cause = ("body-side: the body produced no observations across "
                     "this interval; every other delivery outage in this "
                     "lane left "
                     f"a backlog of at least "
                     f"{round(control['min_ratio'] * 100, 1)}% of its "
                     "expected "
                     "samples while this interval left "
                     f"{covering['samples_inside']} of "
                     f"{covering['expected_samples']}, so the link withheld "
                     "no production because there was none to withhold")
        elif share > 0.0:
            attribution = "not-separable"
            cause = ("not separable: the body produced no observations across "
                     "this interval and the host received nothing across "
                     f"{round(share * 100, 1)}% of it, so the delivered lane "
                     "cannot separate a body-side pause from a link-side outage")
        else:
            attribution = "body-side"
            cause = ("body-side: the body produced no observations across this "
                     "interval of its own clock while the host kept receiving")
        if reboot is not None:
            # The reboot is its own evidence and does not depend on the
            # verdict above, so it is published with every attribution.
            cause += (f"; the device's own monotonic counter fell "
                      f"{reboot['counter_fall_s']:.0f} s across it, so the "
                      "device rebooted, its last boot at "
                      f"{reboot['boot_estimate']} on the device's own clock, "
                      f"and it had been up "
                      f"{reboot['uptime_at_first_row_s']:.0f} s when the app "
                      "produced its first row afterwards")
        if drops_here:
            cause += (f"; the app's own spool-overflow notice is dated inside "
                      f"this interval ({len(drops_here)} notice(s), the app's "
                      "cumulative dropped count reaching "
                      f"{max(notice['dropped'] for notice in drops_here)}), so "
                      "the app's spool was over its bound then and the app was "
                      "holding production it could not deliver, which "
                      "contradicts the reading that there was none to "
                      "withhold; the notice's epoch is the moment of the drop "
                      "and the app discards its oldest rows, so it does not "
                      "date the rows it discarded")
        elif spool_notices:
            cause += ("; the delivered queue carries the app's own "
                      "spool-overflow record, but none of its notices is "
                      "dated inside this interval, so it does not say the app "
                      "dropped rows here")
        else:
            cause += ("; the host's journal shows no accepted POST across "
                      "the interval and the delivered queue carries none of "
                      "the app's own spool-overflow record, so no production "
                      "the app dropped is visible here - but the notice "
                      "reaches the host only if the app uploads after "
                      "dropping, so its absence does not establish that the "
                      "app dropped nothing")
        body_holes.append({
            "start_phone": _iso(start),
            "end_phone": _iso(end),
            "start_utc": _iso(to_host(start)) if aligned else None,
            "end_utc": _iso(to_host(end)) if aligned else None,
            "duration_s": round(end - start, 3),
            "missing_s": round(end - start - cadence, 3),
            "link_silent_share": round(share, 4) if share is not None else None,
            "verdict_holds_unless_clock_differs_by_s": round(margin, 3),
            "samples_inside": samples_inside(to_host(start), to_host(end)),
            "expected_samples": round(expected),
            "resumption_kind": resumption_kind,
            "attribution": attribution,
            "reboot_inside": reboot,
            "spool_drops_inside": drops_here,
            "cause": cause,
        })

    missing_s = _missing_time(series, cadence)
    link_silent_s = sum(hole[1] - hole[0] for hole in link_silence)
    if host_span is not None:
        host_span_s = host_span[1] - host_span[0]
        live_coverage = (round(1.0 - min(link_silent_s, host_span_s) / host_span_s, 6)
                         if host_span_s > 0 else None)
    else:
        host_span_s = None
        live_coverage = None

    return {
        "status": "ok",
        "queue": {
            "path": queue["path"],
            "rows": len(rows),
            "malformed_rows": queue["malformed_rows"],
            "malformed_samples": queue["malformed_samples"],
            "sequence_contiguous": contiguous,
            "first_sequence": sequences[0],
            "last_sequence": sequences[-1],
            "spool_overflow_rows": queue.get("spool_overflow_rows", 0),
            "spool_overflow_notices": queue.get("spool_overflow_notices", []),
            "spool_overflow_note": _SPOOL_EVIDENCE,
        },
        "cadence": {
            "channel": channel,
            "interval_s": round(cadence, 3),
            "samples": len(series),
        },
        "clock_steps": steps,
        "alignment": {
            "status": "measured" if aligned else "unverified",
            "offset_nominal_s": offset,
            "offset_spread_s": round(offset_spread, 3),
            "uncertainty_s": round(uncertainty, 3),
            "note": "the body's readings are the phone's own clock; where the "
                    "offset is not measured they are compared with the host's "
                    "clock only within the body's observed clock instability",
        },
        "span": {
            "start_phone": _iso(span_start),
            "end_phone": _iso(span_end),
            "start_utc": _iso(to_host(span_start)) if aligned else None,
            "end_utc": _iso(to_host(span_end)) if aligned else None,
            "duration_s": round(span_s, 3),
        },
        "body_holes": body_holes,
        "link_holes": link_holes,
        "backlog_control": control,
        "device_boots": boots,
        "coverage": {
            "observation_coverage": (round(1.0 - missing_s / span_s, 6)
                                     if span_s > 0 else None),
            "observation_coverage_clock": "the body's own clock",
            "live_delivery_coverage": live_coverage,
            "live_delivery_coverage_clock": "the host's clock",
            "body_missing_s": round(missing_s, 3),
            "body_silent_s": round(sum(hole["missing_s"] for hole in body_holes), 3),
            "link_silent_s": round(link_silent_s, 3),
            "host_span_s": round(host_span_s, 3) if host_span_s else None,
            "link_state": "measured" if host_span is not None else "unknown",
            "note": "observation_coverage is the fraction of the body's own span "
                    "in which it was producing observations, counting every gap "
                    "beyond one cadence step; live_delivery_coverage is the "
                    "fraction of the host's own span in which it was receiving, "
                    "and is UNKNOWN when the host journal holds no posts for "
                    "this node",
        },
        "host_posts": len(posts),
    }


def _clock_identifiability(lag: dict | None, clock_status: str | None = None,
                           offset_nominal_s: float | None = None,
                           offset_min_s: float | None = None,
                           offset_max_s: float | None = None) -> dict:
    """Why the phone clock is not recoverable from the delivered lane.

    A delivered row carries the phone's clock reading and the host's receipt
    time.  A constant transport delay and a constant phone clock offset
    produce identical rows, so the delivered lane cannot tell the two apart.
    Only a third time reference - the phone's own clock read at a known host
    instant, which the clock-domain document measures - can separate them.

    Where that third reference exists, the observed lag does decompose: the
    document's offset is ``phone clock - host clock``, and the lag is
    ``host receipt - phone clock reading``, so the transport delay is the lag
    plus the offset.
    """
    result = {
        "status": "not-identifiable-from-the-delivered-lane",
        "reason": "a constant transport delay and a constant phone clock offset "
                  "produce identical delivered rows, so the offset cannot be "
                  "separated from the delay without a third time reference",
        "observed_lag_s": lag,
        "third_reference": "the clock-domain document, which reads the phone's "
                           "clock at a host instant and therefore needs a path "
                           "to the phone",
    }
    if clock_status == "ok" and offset_nominal_s is not None and lag:
        delay = {key: round(value + offset_nominal_s, 3)
                 for key, value in lag.items() if key.endswith("_s")}
        result["offset_nominal_s"] = offset_nominal_s
        result["transport_delay_s"] = delay
        low = delay.get("min_s")
        high = delay.get("max_s")
        if low is not None and high is not None and high < 0:
            # A transport delay cannot be negative: the measured offset and the
            # observed lag do not describe the same lane.  Say so instead of
            # publishing an impossible delay.
            result["status"] = "inconsistent-with-the-measured-offset"
            result["reason"] = ("the observed lag and the measured offset imply a "
                                "negative transport delay, so they do not describe "
                                "the same lane and the delay is UNKNOWN")
        else:
            result["status"] = "decomposed-with-the-measured-offset"
            result["reason"] = ("the phone clock is measured for this node, so the "
                                "observed lag separates into that offset and the "
                                "transport delay")
    return result


def _behind_phrase(value: float) -> str:
    """A bound on the clock error, with its sign said the right way round.

    A negative bound is not "at most -1 s behind": it says the phone's clock
    was *ahead* of the host's by at least that much.
    """
    if value < 0:
        return f"at least {abs(value):.3f} s ahead of the host"
    return f"at most {value:.3f} s behind the host"


def _bound_text(bound: dict) -> str:
    """The bound, in words, with the side that is unresolved named.

    The session-wide figure and the figure at the freshest sample answer
    different questions, and both are said: the first holds at every row
    that carries the phone's own monotonic reading, the second holds at the
    sample it was measured at.

    The age that is said with them is the age of the host's **receipt**,
    not of the sample: the lane knows when it received the rows and does
    not know exactly when the phone produced them, so the two must not be
    confused.  The delivery-latency figure beside the bound is what says
    how long before the receipt the sample was produced.
    """
    session_wide = bound.get("behind_upper_s")
    at_sample = bound.get("behind_upper_at_last_sample_s")
    age = bound.get("host_receipt_age_seconds")
    withheld = bound.get("session_wide_withheld_reason")
    receipt = (f"the host received the last row {age:.0f} s ago"
               if age is not None else None)
    if session_wide is None:
        if at_sample is None:
            if not bound.get("freshest_sample_established"):
                return ("not bounded by the delivered lane: the lane does not "
                        "establish which row was produced last")
            return "not bounded by the delivered lane"
        text = f"{_behind_phrase(at_sample)} at the freshest delivered sample"
        if receipt:
            text += f", and {receipt}"
        if withheld:
            text += f", with no session-wide bound because {withheld}"
        return text
    covered = bound.get("monotonic_reference_rows")
    text = (f"{_behind_phrase(session_wide)} at every one of the {covered} "
            f"rows that carry the phone's own monotonic reading")
    if at_sample is not None:
        text += f", and {_behind_phrase(at_sample)} at the freshest of them"
        if receipt:
            text += f", and {receipt}"
    return text


def _delivered_lane(body: Path, node: str) -> dict:
    """The delivered lane's rows, receipts and pairing, parsed once.

    The lane is the whole delivered history, so it is read once per node and
    both the clock bound and the per-row ages are derived from the same
    parse.  The pairing is the part that is easy to get wrong: it walks
    backward from the live tail, because every receipt states how many rows
    its POST appended.

    The journal is read *after* the queue, and the instant it was read is
    kept: the journal is the lane's own record of what the host had received
    by then, and the ages of the delivered rows are measured against that
    instant rather than against an earlier one.  A lane that keeps
    delivering while it is read would otherwise make its own freshest rows
    look older than they are.
    """
    try:
        module = _clock_bound_module()
    except (ImportError, OSError) as error:
        return {"status": "unavailable",
                "reason": f"the clock-bound instrument could not be loaded: {error}"}
    queues = body / "queues"
    try:
        rows_text = (queues / f"{node}.observations.log").read_text(
            encoding="utf-8", errors="replace")
        writes_text = (queues / "writes.jsonl").read_text(
            encoding="utf-8", errors="replace")
    except OSError as error:
        return {"status": "unavailable",
                "reason": f"the delivered lane could not be read: {error}"}
    read_at = time.time()

    rows = module.parse_rows(rows_text)
    receipts = [receipt for receipt in module.parse_receipts(writes_text)
                if receipt.node == node and receipt.kind == "observations"]
    pairing = module.pair_lane(rows, receipts)
    chunks = pairing.chunks
    unpaired = pairing.unpaired_rows
    return {
        "status": "ok",
        "module": module,
        "chunks": chunks,
        "unpaired_head_rows": unpaired,
        "pairing": pairing,
        "read_at": read_at,
        "read_at_utc": _iso(read_at),
        "measurement": module.measure_chunks(chunks, unpaired, pairing=pairing),
    }


#: How many of the delivered POSTs the reading names.  The whole index is one
#: line per POST of the delivered history, which is far more than a reading
#: can carry; the newest entries are the ones that show the lane's live lag.
AGE_INDEX_TAIL = 12


#: Which clock the boot instant is stated in.  It is not the host's.
_BOOT_FRAME = ("the device's own clock, not the host's: the estimate is the "
               "device's clock reading minus its own monotonic counter")

#: What moves and what does not when the device's wall clock is corrected.
#: The instant moves with the correction; the reboot, the counter's fall,
#: the uptime and the host bracket do not.
_BOOT_INSTANT_LIMIT = (
    "the boot instant is stated in the device's own clock frame, so a "
    "correction of that clock applied after the boot moves it by the same "
    "amount; the delivered lane can see such a correction inside a session "
    "(the boot estimate's spread) but not one made during a silence it does "
    "not cover.  What does not move is the reboot itself: the counter's "
    "fall, the uptime the device had when the app produced its first row "
    "afterwards, and the host bracket - the window between the host receipt "
    "of the last row before the silence and the host receipt of the first "
    "row after it - in which the reboot must have happened"
)


def _device_boots(lane: dict | None) -> dict:
    """The device's own account of its boots, read from the delivered lane.

    ``phone_elapsed_ms`` is the device's monotonic clock since boot, so it
    never falls within one boot.  A fall across a session boundary is
    therefore the device rebooting: the alternative, a delivery batch written
    in reverse device-time order, cannot advance the device's own clock by
    the hours a reboot gap carries.

    The instant of the last boot is the device's clock reading minus that
    counter.  That is an instant in the *device's* clock frame, and it moves
    with a correction of that clock: a wall-clock step applied after the boot
    moves the estimate by the same amount, so the estimate cannot be read as
    an instant on the host's timeline.  What does not move is the reboot
    itself - the counter's fall, the uptime the device had when the app
    produced its first row afterwards, and the window between the host
    receipt of the last row before the silence and the host receipt of the
    first row after it, in which the reboot must have happened.  Those are
    published beside the estimate, with the estimate's frame named.
    """
    measurement = (lane or {}).get("measurement") or {}
    sessions = measurement.get("session_objects") or []
    if not sessions:
        return {"status": "unavailable",
                "reason": "the delivered lane holds no paired session to read "
                          "the device's monotonic counter from"}
    reads: list[dict] = []
    for session in sessions:
        covered = session.covered_pairs()
        walls = session.wall_values()
        if not covered or not walls:
            continue
        first_pair, first_elapsed = covered[0]
        last_pair, last_elapsed = covered[-1]
        estimates = [value for _, value in walls]
        reads.append({
            "session": session.session_id,
            "rows": len(session.pairs),
            "device_start": _iso(float(first_pair.device_epoch)),
            "device_end": _iso(float(last_pair.device_epoch)),
            "host_start": _iso(float(first_pair.host_epoch)),
            "host_end": _iso(float(last_pair.host_epoch)),
            "uptime_first_s": round(first_elapsed / 1000.0, 3),
            "uptime_last_s": round(last_elapsed / 1000.0, 3),
            "boot_estimate_start": _iso(min(estimates)),
            "boot_estimate_end": _iso(max(estimates)),
            "boot_estimate_spread_s": round(max(estimates) - min(estimates), 3),
            "opened_after_reboot": bool(session.opened_after_reboot),
            "reboots_in_session": list(session.reboots),
            "_boot_epoch": min(estimates),
            "_device_epoch_start": float(first_pair.device_epoch),
            "_device_epoch_end": float(last_pair.device_epoch),
            "_host_epoch_start": float(first_pair.host_epoch),
            "_host_epoch_end": float(last_pair.host_epoch),
        })
    boots: list[dict] = []
    for older, newer in zip(reads, reads[1:]):
        fall = older["uptime_last_s"] - newer["uptime_first_s"]
        if fall <= 0:
            continue
        if newer["_device_epoch_start"] <= older["_device_epoch_end"]:
            # The device's own clock did not advance across the boundary, so
            # a falling counter is a delivery batch written in reverse
            # device-time order rather than a reboot.
            continue
        boots.append({
            "reboot": True,
            "session_before": older["session"],
            "session_after": newer["session"],
            "device_epoch_of_first_row": newer["device_start"],
            "uptime_before_s": older["uptime_last_s"],
            "uptime_after_s": newer["uptime_first_s"],
            "counter_fall_s": round(fall, 3),
            "uptime_at_first_row_s": newer["uptime_first_s"],
            "boot_estimate": newer["boot_estimate_start"],
            "boot_estimate_frame": _BOOT_FRAME,
            "boot_estimate_epoch_s": round(newer["_boot_epoch"], 3),
            "boot_estimate_before": older["boot_estimate_start"],
            "boot_estimate_delta_s": round(newer["_boot_epoch"] - older["_boot_epoch"], 3),
            "host_bracket_utc": [_iso(older["_host_epoch_end"]),
                                  _iso(newer["_host_epoch_start"])],
            "instant_limit": _BOOT_INSTANT_LIMIT,
            "premise": "the counter is the device's own monotonic clock since "
                       "boot and does not fall within one boot, so a fall "
                       "across a session boundary whose device clock advanced "
                       "is a reboot rather than a reverse-order batch",
        })
    for read in reads:
        read.pop("_boot_epoch", None)
        read.pop("_device_epoch_start", None)
        read.pop("_device_epoch_end", None)
        read.pop("_host_epoch_start", None)
        read.pop("_host_epoch_end", None)
    return {
        "status": "ok",
        "sessions": reads,
        "reboots": boots,
        "note": _BOOT_INSTANT_LIMIT,
    }


#: What a row's age does and does not say.  The delivery age is exact; the
#: observation's own age is older by that row's transport delay, which the
#: delivered lane does not recover, and the lane does not bound the phone's
#: clock from the ahead side, so it does not bound that age from above.
ROW_AGE_LIMITS = (
    "a row's delivery age is exact on the host clock and is the lower bound "
    "of the observation's own age; the observation is older by that row's "
    "transport delay, which the delivered lane does not recover, and the lane "
    "does not bound how far the phone's clock may be ahead of the host, so it "
    "does not bound the observation's age from above"
)


def _age_span(ages: list[dict]) -> tuple[float, float] | None:
    """The newest and oldest delivery age in a table of row ages.

    A row that was received after the reference instant carries no age, so
    it is left out of the span rather than counted as a negative age.
    """
    values = [entry["delivery_age_s"] for entry in ages
              if isinstance(entry.get("delivery_age_s"), (int, float))]
    if not values:
        return None
    return min(values), max(values)


def _delivered_row_ages(lane: dict, lane_now: float, window_rows: list[dict],
                        window_now: float) -> dict:
    """Every delivered row's age, from that row's own receipt.

    The current window's rows carry their own host receipt, so their age is
    exact there.  The delivered history's rows do not, so their age comes
    from the pairing with the accepted-POST journal - published here once per
    POST, so a consumer does not re-derive the pairing and get it wrong.

    Two reference instants, one per source, because the lane keeps
    delivering while it is read: the window's ages are measured against the
    instant the window file was read, and the history's ages against the
    instant the journal was read.  Both instants are published, so a reader
    can tell how far apart they are.
    """
    if lane.get("status") != "ok":
        return {"status": "unavailable", "reason": lane.get("reason")}
    module = lane["module"]
    ages: list[dict] = []
    for row in window_rows:
        if not isinstance(row, dict):
            continue
        collected = row.get("collected")
        received = row.get("received")
        if not isinstance(collected, (int, float)) or not isinstance(received, (int, float)):
            continue
        delivery_age = window_now - float(received)
        entry = {
            "sequence": row.get("sequence"),
            "kind": row.get("kind"),
            "sample_utc": _iso(float(collected)),
            "receipt_utc": _iso(float(received)),
            "observed_s": round(float(received) - float(collected), 3),
        }
        if delivery_age < 0:
            # The row was received after the reference instant, so it had no
            # age at that instant.  A negative age would read as an age, so
            # the row carries the fact instead.
            entry["delivery_age_s"] = None
            entry["received_after_reference_s"] = round(-delivery_age, 3)
        else:
            entry["delivery_age_s"] = round(delivery_age, 3)
            entry["sample_age_s"] = round(window_now - float(collected), 3)
        ages.append(entry)
    profile = module.age_profile(lane["chunks"], lane_now,
                                 lane["unpaired_head_rows"],
                                 pairing=lane.get("pairing"))
    index = module.age_index(lane["chunks"], lane_now)
    # The window's rows carry the reader's own poll time; the journal carries
    # the host's accepted-POST time.  Both are host instants, and they are
    # two different instants: the reader reads a page the host has already
    # received.  Where the index carries the same row, the gap between them
    # is measured rather than assumed.
    for entry in ages:
        sequence = entry.get("sequence")
        receipt_utc = entry.get("receipt_utc")
        if not isinstance(sequence, int) or not isinstance(receipt_utc, str):
            continue
        found = module.age_index_lookup(index, sequence)
        if found is None:
            continue
        entry["host_arrival_epoch_s"] = found["receipt_epoch"]
        entry["host_arrival_age_s"] = round(window_now - found["receipt_epoch"], 3)
        entry["reader_poll_delay_s"] = round(
            datetime.fromisoformat(receipt_utc).timestamp() - found["receipt_epoch"], 3)
    history = dict(profile)
    history["index_posts"] = len(index)
    history["index_tail"] = index[-AGE_INDEX_TAIL:]
    return {
        "status": "ok",
        "window": {"rows": len(ages), "ages": ages,
                   "reference_utc": _iso(window_now)},
        "history": history,
        "history_reference_utc": _iso(lane_now),
        "receipt_sources": {
            "window": "the reader's own poll time, stamped when it read the "
                      "page that carried the row",
            "history": "the host's accepted-POST time, stamped when the row "
                       "arrived at the host",
        },
        "limits": ROW_AGE_LIMITS,
    }


def _delivered_clock_bound(lane: dict, now: float) -> dict:
    """What the delivered lane can say about this body's phone clock.

    A delivered row carries the phone's own clock reading and the host's
    receipt time.  The difference is the phone's clock error plus a transport
    delay that cannot be negative, so every row bounds the phone's clock
    from above: the phone is at most that far *behind* the host at that row.
    The lane does not bound the other side - how far the phone may be *ahead*
    of the host needs a device-side clock read, which this lane does not have.

    Two numbers are published.  The bound at the freshest delivered sample
    holds with no premise at all.  The session-wide bound holds only when the
    phone's uptime counter shows no reset inside the session - a reset would
    re-read the wall clock and the rows on either side of it would not share
    one monotonic offset - and it carries the premise it rests on.

    The reading also publishes the drift of the phone's wall clock against
    its own monotonic clock, and the lower bound on the delivery delay that
    follows from the bound, because those two are what tell a reader that a
    stale timestamp is the lane being late rather than the phone's clock
    being wrong.
    """
    if lane.get("status") != "ok":
        return {"status": "unavailable", "reason": lane.get("reason")}
    sessions = lane["measurement"]["session_objects"]
    if not sessions or not sessions[-1].pairs:
        return {"status": "unavailable",
                "reason": "the delivered lane holds no paired rows for this node"}
    current = sessions[-1]
    summary = current.summary()
    freshest = current.freshest_pair
    last_receipt = current.last_receipt_epoch
    return {
        "status": "bounded-from-the-delivered-lane",
        "session": summary["session"],
        "rows": summary["rows"],
        "monotonic_reference_rows": summary["monotonic_reference_rows"],
        "behind_upper_s": summary["behind_upper_s"],
        "behind_upper_at_last_sample_s": summary["behind_upper_at_last_sample_s"],
        "behind_lower_s": None,
        "behind_level_now_s": summary["behind_level_now_s"],
        "level_change_s": summary["level_change_s"],
        "wall_drift_s": summary["wall_drift_s"],
        "clock_step": summary["clock_step"],
        "delivery_latency_lower_s": summary["delivery_latency_lower_s"],
        "lateness": summary["lateness"],
        "ambiguous_resets": summary["ambiguous_resets"],
        "masked_resets": summary["masked_resets"],
        "premise": summary["premise"],
        "session_wide_withheld_reason": current.session_wide_withheld_reason,
        "observed_min_s": summary["observed_min_s"],
        "freshest_sample_established": freshest is not None,
        "observed_at_epoch_s": (freshest.host_epoch if freshest is not None
                                else last_receipt),
        "last_receipt_epoch_s": last_receipt,
        "host_receipt_age_seconds": (round(now - last_receipt, 3)
                                      if last_receipt is not None else None),
    }


#: How long a delivered row may wait before the hour that produced it is
#: reported as backlogged rather than live.  It is a parameter because the
#: lane's own cadence sets it: a body that posts every five seconds is not
#: backlogged by a minute, and a body that posts once an hour is.
DAY_SHAPE_BACKLOG_S = 7200.0

#: How many hours of the day-shape table the rendered reading carries.  The
#: whole table is published in the report, but a reading is bounded prose: the
#: hours with the largest waits are the ones that carry the day, so those are
#: the ones rendered.
DAY_SHAPE_HOURS_RENDERED = 12


def _hour_key(epoch: float) -> str:
    """The UTC hour an instant falls in, as ``YYYY-MM-DD HH``."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H")


def _day_shape(lane: dict, backlog_s: float = DAY_SHAPE_BACKLOG_S) -> dict:
    """The human's day, as the delivered lane carries it.

    The lane is read in two hours at once, because the human's day and the
    host's delivery are two different clocks:

    * the **phone's own clock** decides the hour a row belongs to, because the
      hour is the human's day;
    * the **host's clock** decides when the row arrived, and the difference
      between the two is the row's wait.

    The acceleration channel's coverage travels with every hour.  A row that
    was delivered without an accelerometer sample is a fact about the
    producer's freshness check, not about the human's motion, so an hour in
    which the phone produced no sample is not an hour in which the human was
    still.  The coverage is therefore published beside the hour, and the
    reader is told which hours carry no sample at all.

    The body's own silences are published as intervals, not as missing table
    rows: an hour that does not appear in the table because the body produced
    nothing is a different fact from an hour whose rows all arrived late.
    """
    if lane.get("status") != "ok":
        return {"status": "unavailable", "reason": lane.get("reason")}
    chunks = lane["chunks"]

    produced: dict[str, int] = {}
    acc_value: dict[str, int] = {}
    acc_unavailable: dict[str, int] = {}
    acc_reasons: dict[str, Counter] = {}
    waits: dict[str, list[float]] = {}
    host_posts: dict[str, int] = {}
    host_rows: dict[str, int] = {}

    for chunk in chunks:
        host_hour = _hour_key(chunk.receipt.host_epoch)
        host_posts[host_hour] = host_posts.get(host_hour, 0) + 1
        host_rows[host_hour] = host_rows.get(host_hour, 0) + len(chunk.rows)
        for row in chunk.rows:
            hour = _hour_key(row.device_epoch)
            produced[hour] = produced.get(hour, 0) + 1
            if row.kind == "acceleration":
                if "unavailable" in row.body:
                    acc_unavailable[hour] = acc_unavailable.get(hour, 0) + 1
                    reason = ("unknown")
                    if "reason=" in row.body:
                        reason = row.body.split("reason=", 1)[1].strip() or "unknown"
                    acc_reasons.setdefault(hour, Counter())[reason] += 1
                else:
                    acc_value[hour] = acc_value.get(hour, 0) + 1
            waits.setdefault(hour, []).append(chunk.receipt.host_epoch - row.device_epoch)

    # The body's own silences: hours inside the delivered span with no row at
    # all.  These are the phone's clock, so they are the human's hours, not
    # the host's.
    silences: list[dict] = []
    if produced:
        first = min(produced)
        last = max(produced)
        cursor = datetime.strptime(first, "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
        end = datetime.strptime(last, "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
        run_start = None
        while cursor <= end:
            label = cursor.strftime("%Y-%m-%d %H")
            if label in produced:
                if run_start is not None:
                    silences.append({"start_hour": run_start, "end_hour": previous,
                                     "hours": _hours_between(run_start, previous)})
                    run_start = None
            elif run_start is None:
                run_start = label
            previous = label
            cursor += timedelta(hours=1)
        if run_start is not None:
            silences.append({"start_hour": run_start, "end_hour": previous,
                             "hours": _hours_between(run_start, previous)})

    hours: list[dict] = []
    for hour in sorted(produced):
        hour_waits = waits.get(hour, [])
        value = acc_value.get(hour, 0)
        unavailable = acc_unavailable.get(hour, 0)
        acc_rows = value + unavailable
        late = [wait for wait in hour_waits if wait > backlog_s]
        reasons = acc_reasons.get(hour)
        hours.append({
            "phone_hour": hour,
            "produced_rows": produced[hour],
            "acceleration_value_rows": value,
            "acceleration_unavailable_rows": unavailable,
            "acceleration_sample_coverage": (round(value / acc_rows, 6) if acc_rows else None),
            "acceleration_unavailable_reasons": dict(reasons) if reasons else {},
            "arrived_within_backlog_window": len(hour_waits) - len(late),
            "arrived_after_backlog_window": len(late),
            "worst_wait_s": round(max(hour_waits), 3) if hour_waits else None,
        })

    total_wait = [wait for values in waits.values() for wait in values]
    backlogged = sum(hour["arrived_after_backlog_window"] for hour in hours)
    no_sample_hours = [hour["phone_hour"] for hour in hours
                       if hour["acceleration_value_rows"] == 0
                       and hour["acceleration_unavailable_rows"] > 0]
    return {
        "status": "ok",
        "phone_clock_hours": len(hours),
        "host_clock_hours": len(host_posts),
        "first_phone_hour": hours[0]["phone_hour"] if hours else None,
        "last_phone_hour": hours[-1]["phone_hour"] if hours else None,
        "produced_rows": sum(produced.values()),
        "backlog_window_s": backlog_s,
        "rows_arrived_after_backlog_window": backlogged,
        "worst_wait_s": round(max(total_wait), 3) if total_wait else None,
        "acceleration_value_rows": sum(acc_value.values()),
        "acceleration_unavailable_rows": sum(acc_unavailable.values()),
        "acceleration_sample_coverage": (
            round(sum(acc_value.values())
                  / (sum(acc_value.values()) + sum(acc_unavailable.values())), 6)
            if (sum(acc_value.values()) + sum(acc_unavailable.values())) else None),
        "hours_without_any_acceleration_sample": len(no_sample_hours),
        "body_silences": silences,
        "hours": hours,
        "host_arrivals": [
            {"host_hour": hour, "posts": host_posts[hour], "rows": host_rows[hour]}
            for hour in sorted(host_posts)
        ],
        "limits": (
            "an hour here is the phone's own clock, so the table is the "
            "human's day and not the host's; the wait is the host receipt "
            "minus the phone's own reading, so it carries the phone's clock "
            "error as well as the transport delay and the lane does not "
            "separate them; and an hour whose acceleration rows carry no "
            "sample is a fact about the producer's freshness check, not a "
            "measurement of the human's stillness"
        ),
    }


def _hours_between(start: str, end: str) -> int:
    """The number of hours from one hour label to another, inclusive."""
    first = datetime.strptime(start, "%Y-%m-%d %H")
    last = datetime.strptime(end, "%Y-%m-%d %H")
    return int((last - first).total_seconds() // 3600) + 1


def _attach_clock_anchor(reading: dict, clock: dict) -> None:
    """Say whether a hole's timestamps are anchored to the host's timeline.

    A hole is a statement about the body's own clock.  Where that clock is
    measured, the hole is anchored to the host's timeline; where it is not,
    the hole carries the phone's clock as an unverified premise.
    """
    continuity = reading.get("continuity") or {}
    if continuity.get("status") != "ok":
        return
    if clock.get("status") == "ok":
        continuity["clock_anchor"] = {
            "status": "anchored",
            "offset_bounds_s": reading.get("clock_offset_bounds_s"),
            "note": "the phone clock is measured for this node, so the hole "
                    "timestamps are anchored to the host's timeline within "
                    "those bounds",
        }
    else:
        continuity["clock_anchor"] = {
            "status": "unanchored",
            "reason": "the phone clock is not measured for this node, so a "
                      "hole's timestamps are only as good as the phone's own "
                      "clock and a forward clock step cannot be excluded",
        }


def node_reading(node: dict, body: Path, max_age: float, now: float,
                 threshold: float) -> dict:
    """One node's evidence-backed reading, with its unknowns named."""
    name = node.get("node") or "unknown"
    delivery = node.get("channel_delivery") or {}
    clock = node.get("clock_domain") or {}
    stillness = node.get("stillness") or {}

    reading: dict = {
        "node": name,
        "session": node.get("session"),
        "transport": node.get("transport"),
        "delivery_status": delivery.get("status"),
        "cursor_state": delivery.get("cursor_state"),
        "cursor_age_seconds": delivery.get("cursor_age_seconds"),
        "delivery_reason": delivery.get("reason"),
        "node_mode": delivery.get("node_mode"),
        "phone_status": node.get("phone_status"),
        "clock_status": clock.get("status"),
        "clock_reason": clock.get("reason"),
        "clock_document": None,
        "clock_offset_nominal_s": clock.get("offset_nominal_s"),
        "clock_offset_bounds_s": [clock.get("offset_min_s"), clock.get("offset_max_s")],
        "clock_samples": clock.get("samples"),
        "stillness_status": stillness.get("status"),
        "stillness_unavailable_inputs": stillness.get("unavailable_inputs"),
        "capabilities": node.get("capabilities"),
        "owner_checkboxes": node.get("owner_checkboxes"),
    }

    # --- current window -------------------------------------------------
    state = _read_json(body / "perception" / f"{name}.json")
    window_read_at = time.time()

    # The delivered lane bounds the phone's clock from above even where no
    # clock-domain document measures it.  The bound is one-sided: it says how
    # far the phone may be *behind* the host, never how far it may be ahead.
    # The lane is parsed once here: the bound and the per-row ages come from
    # the same pairing.
    lane = _delivered_lane(body, name)
    # The lane keeps delivering while it is read, so each source's ages are
    # measured against the instant that source was read - never against an
    # earlier one, which would make the freshest rows look older than they
    # are, or even younger than the reading itself.
    window_now = max(now, window_read_at)
    lane_now = max(now, lane.get("read_at") or window_now)
    reading["window_reference_utc"] = _iso(window_now)
    if clock.get("status") != "ok":
        reading["clock_bound"] = _delivered_clock_bound(lane, lane_now)

    if state is None:
        reading["current_window"] = False
        reading["clock_identifiability"] = _clock_identifiability(
            None, clock.get("status"), clock.get("offset_nominal_s"),
            clock.get("offset_min_s"), clock.get("offset_max_s"))
        reading["unknowns"] = ["no delivered perception state file for this node"]
        if clock.get("status") != "ok":
            reading["unknowns"].append(_CLOCK_NOT_IDENTIFIABLE)
        reading["continuity"] = _continuity(body, name, threshold, clock, lane)
        _attach_clock_anchor(reading, clock)
        reading["unknowns"].extend(_continuity_unknowns(reading["continuity"]))
        reading["row_ages"] = _delivered_row_ages(lane, lane_now, [], window_now)
        reading["day_shape"] = _day_shape(lane)
        return reading

    history = state.get("history")
    history = history if isinstance(history, list) else []
    span = _row_span(history)
    reading["window"] = span
    reading["cursor"] = state.get("cursor")
    reading["row_ages"] = _delivered_row_ages(lane, lane_now, history, window_now)
    reading["day_shape"] = _day_shape(lane)

    receipts = [row.get("received") for row in history
                if isinstance(row.get("received"), (int, float))]
    newest = max(receipts) if receipts else None
    age = (window_now - newest) if newest is not None else None
    reading["host_receipt_age_seconds"] = round(age, 3) if age is not None else None
    current = (delivery.get("status") == "pass" and age is not None and age <= max_age)
    reading["current_window"] = current

    by_kind: dict[str, list[dict]] = {}
    for row in history:
        if not isinstance(row, dict):
            continue
        by_kind.setdefault(str(row.get("kind")), []).append(row)

    measurements: dict = {}
    if by_kind.get("acceleration"):
        measurements["acceleration"] = _acceleration_summary(by_kind["acceleration"])
    if by_kind.get("light"):
        measurements["light"] = _light_summary(by_kind["light"])
    if by_kind.get("battery"):
        measurements["battery"] = _battery_summary(by_kind["battery"])
    if by_kind.get("wifi_link_rssi_dbm"):
        measurements["wifi_link_rssi_dbm"] = _other_summary(by_kind["wifi_link_rssi_dbm"])
    reading["measurements"] = measurements

    availability = {kind: _channel_availability(kind, rows)
                    for kind, rows in by_kind.items() if kind in CHANNEL_VALUE_FIELD}
    reading["channel_availability"] = availability

    # The clock-domain document may exist and still carry no measurement.
    # Its reason names the missing prerequisite, so it is reported instead of
    # being collapsed into "no document".
    document = _read_json(body / f"clock-domain-{name}.json")
    if document is not None:
        reading["clock_document"] = {
            "status": document.get("status"),
            "reason": document.get("reason"),
            "observed_at_epoch_s": document.get("observed_at_epoch_s"),
            "samples": document.get("samples"),
        }
        if clock.get("status") != "ok" and not reading["clock_reason"]:
            reading["clock_reason"] = document.get("reason")

    reading["transitions"] = _event_transitions(body, name)
    reading["continuity"] = _continuity(body, name, threshold, clock, lane)
    reading["clock_identifiability"] = _clock_identifiability(
        _delivery_lag(history), clock.get("status"), clock.get("offset_nominal_s"),
        clock.get("offset_min_s"), clock.get("offset_max_s"))
    _attach_clock_anchor(reading, clock)

    # --- gates ----------------------------------------------------------
    unknowns: list[str] = []
    bound = reading.get("clock_bound") or {}
    bound_status = bound.get("status")
    if clock.get("status") != "ok":
        reason = reading.get("clock_reason")
        detail = f": {reason}" if reason else ""
        if bound_status == "bounded-from-the-delivered-lane":
            session = bound.get("session") or "unknown-session"
            unknowns.append(
                "absolute phone time is UNKNOWN for this node: the delivered "
                "lane bounds the phone's clock from above only, so it says how "
                f"far the phone may be behind ({_bound_text(bound)}; session "
                f"{session}, {bound.get('rows')} paired rows) and not how far "
                f"it may be ahead{detail}: only the host receipt anchors a "
                "reading here")
        else:
            unknowns.append(
                "absolute phone time is UNKNOWN for this node (no measured "
                f"phone/host clock offset{detail}): only host receipt anchors "
                "a reading here")
    if not current:
        unknowns.append(
            "this node has no current delivered window, so it cannot carry a "
            "present observation")
    for kind, state in sorted(availability.items()):
        if state.get("status") != "unavailable":
            continue
        reasons = ", ".join(state.get("reasons") or [])
        unknowns.append(
            f"the {kind} channel is unavailable (reason: {reasons}): it "
            "reports why it has no value, so it carries no observation of "
            "that channel - it is not a quiet channel")
    rate = (measurements.get("acceleration") or {}).get("sample_rate_hz")
    if rate is not None and rate < GAIT_RESOLUTION_HZ:
        unknowns.append(
            f"acceleration sampling is {rate} Hz, below the {GAIT_RESOLUTION_HZ} Hz "
            "needed to resolve gait: no activity, motion or posture label is "
            "supportable from this window")
    if stillness.get("status", "").startswith("unknown"):
        unknowns.append(
            f"composite stillness is {stillness.get('status')} (not a presence "
            "or occupancy claim)")
    unknowns.extend(_continuity_unknowns(reading["continuity"]))
    day = reading.get("day_shape") or {}
    if day.get("status") == "ok":
        no_sample = day.get("hours_without_any_acceleration_sample") or 0
        coverage = day.get("acceleration_sample_coverage")
        if no_sample:
            unknowns.append(
                f"{no_sample} hour(s) of the delivered history carry no "
                "accelerometer sample at all: the producer published the "
                "channel as unavailable, so those hours measure the "
                "producer's freshness check and not the human's motion - "
                "counting them as still minutes would read the producer as "
                "the body")
        if coverage is not None and coverage < 1.0:
            unknowns.append(
                f"the acceleration channel carries a sample on only "
                f"{coverage * 100:.1f}% of its delivered rows, so any statement "
                "about the human's motion over this history must name the "
                "window it was measured over and the coverage that window had; "
                "a missing acceleration row is the producer's sampling "
                "behaviour and not a measurement of the human's stillness")
    if clock.get("status") != "ok":
        unknowns.append(_CLOCK_NOT_IDENTIFIABLE)
        if bound_status == "bounded-from-the-delivered-lane":
            unknowns.append(
                "how far the phone's clock may be AHEAD of the host is "
                "unresolved: the delivered lane cannot separate the phone's "
                "clock error from the transport delay, and a device-side clock "
                "read is what would")
            if bound.get("behind_upper_s") is None:
                unknowns.append(
                    "no session-wide clock bound holds for this node: "
                    f"{bound.get('session_wide_withheld_reason') or 'the lane does not support one'}")
    reading["unknowns"] = unknowns
    claims = (["a current, host-anchored reading of this body's own sensed channels"]
              if current else [])
    if clock.get("status") == "ok":
        claims.append("the body's own timestamps are anchored to the host "
                      "timeline within the measured clock offset, so its "
                      "observations can be placed on the host's clock")
    elif bound_status == "bounded-from-the-delivered-lane":
        claim = (f"the phone's clock is {_bound_text(bound)}, measured over "
                 f"session {bound.get('session') or 'unknown-session'} "
                 f"({bound.get('rows')} paired rows, "
                 f"{bound.get('monotonic_reference_rows')} of them carrying the "
                 "phone's own monotonic reference)")
        if bound.get("behind_upper_s") is not None:
            claim += ("; the session-wide bound assumes the phone's own "
                      "monotonic clock tracks true time and that those rows "
                      "share one monotonic offset, neither of which the "
                      "delivered lane can test")
            drift = bound.get("wall_drift_s")
            latency = bound.get("delivery_latency_lower_s")
            if drift is not None and latency is not None:
                claim += (f"; the phone's wall clock moved at most {drift:.3f} s "
                          f"against its own monotonic clock over the session, so "
                          f"the staleness of the phone's timestamps is the lane's "
                          f"lateness rather than the phone's clock: at its worst "
                          f"the lane delivered a row at least {latency:.1f} s "
                          f"after the phone produced it")
            lateness = bound.get("lateness") or {}
            if lateness.get("rows"):
                claim += (f"; that lateness is not one figure: over the session it "
                          f"spans {lateness['observed_min_s']:.2f} s to "
                          f"{lateness['observed_max_s']:.0f} s, with a median of "
                          f"{lateness['observed_median_s']:.1f} s and "
                          f"{lateness['stale_rows']} of {lateness['rows']} rows "
                          f"more than {lateness['stale_threshold_s']:.0f} s old, so "
                          f"the staleness is a queue backlog rather than a constant "
                          f"delay")
                if not lateness.get("constant_correction_within_tolerance"):
                    claim += (f" and one correction cannot age the whole session "
                              f"(the worst residual is "
                              f"{lateness['relative_age_residual_s']:.0f} s)")
                claim += ", so a row's age has to come from that row's own receipt"
        else:
            tightest = bound.get("observed_min_s")
            if tightest is not None:
                claim += (f"; the tightest bound the session's rows support is "
                          f"{tightest:+.3f} s, at the row that produced it")
        claims.append(claim)
    ages = reading.get("row_ages") or {}
    if ages.get("status") == "ok":
        window_ages = (ages.get("window") or {}).get("ages") or []
        history = ages.get("history") or {}
        claim = "every delivered row carries its own age"
        span = _age_span(window_ages)
        if span is not None:
            newest, oldest = span
            claim += (f": the current window's {len(window_ages)} rows are "
                      f"{newest:.1f} s to {oldest:.1f} s old")
        if history.get("rows"):
            claim += (f", and the delivered history of {history['rows']} rows "
                      f"reaches back {history['oldest_row_sample_age_s']:.0f} s "
                      f"in the phone's own clock frame")
        claim += ("; that age is the age of the delivery, and it is the lower "
                  "bound of the observation's own age - the observation is older "
                  "by that row's transport delay, which the lane does not "
                  "recover")
        claims.append(claim)
    day = reading.get("day_shape") or {}
    if day.get("status") == "ok":
        claim = ("the human's day is readable hour by hour in the phone's own "
                 f"clock: {day['phone_clock_hours']} hours carry "
                 f"{day['produced_rows']} delivered rows")
        coverage = day.get("acceleration_sample_coverage")
        if coverage is not None:
            claim += (f", and the acceleration channel carries a sample on "
                      f"{coverage * 100:.1f}% of its rows")
        if day.get("rows_arrived_after_backlog_window"):
            claim += (f"; {day['rows_arrived_after_backlog_window']} rows reached "
                      f"the host more than {day['backlog_window_s']:.0f} s after "
                      "the phone produced them, so they were not live when they "
                      "arrived")
        if day.get("body_silences"):
            longest = max(day["body_silences"], key=lambda s: s["hours"])
            claim += (f"; the body produced nothing at all across "
                      f"{len(day['body_silences'])} interval(s), the longest "
                      f"{longest['hours']} h from {longest['start_hour']} to "
                      f"{longest['end_hour']} in the phone's own clock")
        claims.append(claim)
    reading["claims_supported"] = claims
    return reading


def _continuity_unknowns(continuity: dict) -> list[str]:
    """What the delivered history cannot settle."""
    if continuity.get("status") != "ok":
        return [f"observation continuity is UNKNOWN ({continuity.get('reason')}): "
                "the delivered history of this body could not be read"]
    unknowns: list[str] = []
    body_holes = continuity.get("body_holes") or []
    link_holes = continuity.get("link_holes") or []
    unmeasured = [hole for hole in body_holes
                  if hole.get("link_silent_share") is None]
    if unmeasured:
        total = sum(hole["duration_s"] for hole in unmeasured)
        unknowns.append(
            f"the body produced no observations across {len(unmeasured)} "
            f"interval(s) totalling {round(total, 1)} s, and the host journal "
            "cannot say whether it was receiving then, so whether the link was "
            "live across those intervals is UNKNOWN")
    shared = [hole for hole in body_holes
              if hole.get("attribution") == "not-separable"]
    if shared:
        total = sum(hole["duration_s"] for hole in shared)
        unknowns.append(
            f"the body produced no observations across {len(shared)} interval(s) "
            f"totalling {round(total, 1)} s, and the host received nothing "
            "across the same intervals: the delivered lane cannot separate a "
            "body-side pause from a link-side outage")
    stopped = [hole for hole in body_holes
               if hole.get("attribution") == "body-side"
               and (hole.get("link_silent_share") or 0.0) > 0.0]
    if stopped:
        total = sum(hole["duration_s"] for hole in stopped)
        unknowns.append(
            f"the body produced no observations across {len(stopped)} interval(s) "
            f"totalling {round(total, 1)} s, and the lane's own other delivery "
            "outages show the link withheld no production because there was "
            "none to withhold; why the body stopped is not in the delivered "
            "lane")
    recovered = [hole for hole in link_holes
                 if hole.get("attribution") == "link-side"]
    if recovered:
        total = sum(hole["duration_s"] for hole in recovered)
        unknowns.append(
            f"the host received nothing for {round(total, 1)} s while the body "
            "kept producing observations; those observations were delivered "
            "afterwards, so the data is recovered but no consumer saw it live")
    return unknowns


def build_report(space: Path, max_age: float, now: float,
                 threshold: float = DEFAULT_HOLE_THRESHOLD_S) -> dict:
    body = _body_dir(space)
    space_doc = _read_json(body / "space.json")
    if space_doc is None:
        return {"status": "UNKNOWN", "reason": "space.json unreadable",
                "space": str(space)}
    nodes = space_doc.get("nodes")
    nodes = nodes if isinstance(nodes, list) else []
    readings = [node_reading(node, body, max_age, now, threshold) for node in nodes
                if isinstance(node, dict)]

    current_nodes = [r["node"] for r in readings if r.get("current_window")]
    stale_nodes = [r["node"] for r in readings if not r.get("current_window")]

    # Fusion needs every body current *and* every body time-anchored; a stale
    # or unclocked body makes a combined claim UNKNOWN, never a quiet one.
    fusion: dict
    if not readings:
        fusion = {"status": "UNKNOWN", "reason": "no nodes in the space view"}
    elif stale_nodes:
        fusion = {
            "status": "UNKNOWN",
            "reason": "not every body holds a current window",
            "current_nodes": current_nodes,
            "stale_nodes": stale_nodes,
        }
    else:
        unclocked = [r["node"] for r in readings if r.get("clock_status") != "ok"]
        if unclocked:
            fusion = {
                "status": "UNKNOWN",
                "reason": "a body has no measured phone/host clock offset, so no "
                          "time-aligned cross-body claim is supported",
                "current_nodes": current_nodes,
                "unclocked_nodes": unclocked,
            }
        else:
            fusion = {"status": "supported", "current_nodes": current_nodes}

    return {
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "space": str(body),
        "space_generated_at_utc": space_doc.get("generated_at_utc"),
        "max_age_seconds": max_age,
        "hole_threshold_seconds": threshold,
        "nodes": readings,
        "current_nodes": current_nodes,
        "stale_nodes": stale_nodes,
        "cross_body_fusion": fusion,
    }


def render(report: dict) -> str:
    lines: list[str] = []
    if report.get("status") != "ok":
        lines.append(f"status={report.get('status')} reason={report.get('reason')}")
        return "\n".join(lines)
    lines.append(f"space: {report['space']}")
    lines.append(f"space generated_at_utc: {report['space_generated_at_utc']}")
    lines.append(f"now: {report['generated_at_utc']}  max_age={report['max_age_seconds']}s")
    for node in report["nodes"]:
        lines.append("")
        lines.append(f"== node {node['node']} (session {node.get('session')})")
        lines.append(f"   transport={node.get('transport')} phone_status={node.get('phone_status')}")
        lines.append(f"   delivery: status={node.get('delivery_status')} "
                     f"cursor_state={node.get('cursor_state')} "
                     f"cursor_age_s={node.get('cursor_age_seconds')} "
                     f"reason={node.get('delivery_reason')} mode={node.get('node_mode')}")
        lines.append(f"   clock: status={node.get('clock_status')} "
                     f"offset_nominal_s={node.get('clock_offset_nominal_s')} "
                     f"bounds={node.get('clock_offset_bounds_s')} "
                     f"samples={node.get('clock_samples')}")
        if node.get("clock_reason"):
            lines.append(f"   clock reason: {node['clock_reason']}")
        document = node.get("clock_document")
        if document:
            lines.append(f"   clock document: status={document.get('status')} "
                         f"samples={document.get('samples')} "
                         f"observed_at_epoch_s={document.get('observed_at_epoch_s')}")
        bound = node.get("clock_bound")
        if bound:
            lines.append(f"   clock bound (delivered lane): "
                         f"status={bound.get('status')} "
                         f"behind_upper_s={bound.get('behind_upper_s')} "
                         f"behind_at_last_sample_s="
                         f"{bound.get('behind_upper_at_last_sample_s')} "
                         f"behind_lower_s={bound.get('behind_lower_s')} "
                         f"session={bound.get('session')} "
                         f"rows={bound.get('rows')} "
                         f"monotonic_rows={bound.get('monotonic_reference_rows')} "
                         f"wall_drift_s={bound.get('wall_drift_s')} "
                         f"delivery_latency_lower_s="
                         f"{bound.get('delivery_latency_lower_s')}")
            if bound.get("reason"):
                lines.append(f"     reason: {bound.get('reason')}")
            lateness = bound.get("lateness") or {}
            if lateness:
                lines.append(f"   lane lateness: rows={lateness.get('rows')} "
                             f"min_s={lateness.get('observed_min_s')} "
                             f"median_s={lateness.get('observed_median_s')} "
                             f"p95_s={lateness.get('observed_p95_s')} "
                             f"max_s={lateness.get('observed_max_s')} "
                             f"spread_s={lateness.get('spread_s')} "
                             f"stale_rows={lateness.get('stale_rows')} "
                             f"stale_threshold_s={lateness.get('stale_threshold_s')} "
                             f"relative_age_residual_s="
                             f"{lateness.get('relative_age_residual_s')} "
                             f"constant_correction_within_"
                             f"{lateness.get('tolerance_s')}s="
                             f"{lateness.get('constant_correction_within_tolerance')}")
            if bound.get("premise"):
                lines.append(f"     premise: {bound.get('premise')}")
            if bound.get("session_wide_withheld_reason"):
                lines.append("     no session-wide bound: "
                             f"{bound.get('session_wide_withheld_reason')}")
            if bound.get("ambiguous_resets"):
                lines.append("     ambiguous resets (a reset and a "
                             "reverse-order batch look alike here): "
                             f"{bound.get('ambiguous_resets')}")
            if bound.get("masked_resets"):
                lines.append("     masked resets (the counter falls when the "
                             "rows are ordered by the phone's own clock): "
                             f"{bound.get('masked_resets')}")
        lines.append(f"   current_window={node.get('current_window')} "
                     f"host_receipt_age_s={node.get('host_receipt_age_seconds')} "
                     f"cursor={node.get('cursor')}")
        ages = node.get("row_ages") or {}
        if ages.get("status") == "ok":
            window_ages = (ages.get("window") or {}).get("ages") or []
            history = ages.get("history") or {}
            lines.append(f"   row ages: window reference="
                         f"{(ages.get('window') or {}).get('reference_utc')} "
                         f"history reference={ages.get('history_reference_utc')}")
            if window_ages:
                span = _age_span(window_ages)
                if span is not None:
                    newest, oldest = span
                    lines.append(f"   row ages: window rows={len(window_ages)} "
                                 f"delivery_age_s={newest:.3f}..{oldest:.3f}")
                for entry in window_ages:
                    lines.append(f"     seq={entry.get('sequence')} "
                                 f"kind={entry.get('kind')} "
                                 f"sample={entry.get('sample_utc')} "
                                 f"receipt={entry.get('receipt_utc')} "
                                 f"delivery_age_s={entry.get('delivery_age_s')} "
                                 f"observed_s={entry.get('observed_s')} "
                                 f"sample_age_s={entry.get('sample_age_s')} "
                                 f"host_arrival_age_s="
                                 f"{entry.get('host_arrival_age_s')} "
                                 f"reader_poll_delay_s="
                                 f"{entry.get('reader_poll_delay_s')}")
            if history.get("rows"):
                lines.append(f"   delivered history: rows={history.get('rows')} "
                             f"posts={history.get('posts')} "
                             f"unpaired_head_rows={history.get('unpaired_head_rows')} "
                             f"newest_delivery_age_s="
                             f"{history.get('newest_row_delivery_age_s')} "
                             f"oldest_delivery_age_s="
                             f"{history.get('oldest_row_delivery_age_s')} "
                             f"oldest_sample_age_s="
                             f"{history.get('oldest_row_sample_age_s')} "
                             f"history_span_s={history.get('history_span_s')}")
                witness = history.get("pairing_witness") or {}
                if witness.get("status") != "unchecked":
                    lines.append(f"     pairing witness: {witness.get('status')} "
                                 f"checked={witness.get('checked')} "
                                 f"repaired={witness.get('repaired')} "
                                 f"unpaired_rows={witness.get('unpaired_rows')}")
                    for repair in witness.get("repairs") or []:
                        lines.append(f"       repair: {repair.get('resolution')} "
                                     f"records={repair.get('records')} "
                                     f"byte_count={repair.get('byte_count')} "
                                     f"rows_bytes={repair.get('rows_bytes')} "
                                     f"first_seq={repair.get('first_seq')} "
                                     f"last_seq={repair.get('last_seq')} "
                                     f"unpaired_rows={repair.get('unpaired_rows')}")
                    contradiction = witness.get("first_contradiction")
                    if contradiction:
                        lines.append(f"       contradiction: "
                                     f"host_epoch={contradiction.get('host_epoch')} "
                                     f"records={contradiction.get('records')} "
                                     f"byte_count={contradiction.get('byte_count')} "
                                     f"rows_bytes={contradiction.get('rows_bytes')}")
            if ages.get("limits"):
                lines.append(f"     limit: {ages['limits']}")
        day = node.get("day_shape") or {}
        if day.get("status") == "ok":
            lines.append(f"   day shape: phone-clock hours={day.get('phone_clock_hours')} "
                         f"host-clock hours={day.get('host_clock_hours')} "
                         f"first={day.get('first_phone_hour')} "
                         f"last={day.get('last_phone_hour')} "
                         f"produced_rows={day.get('produced_rows')}")
            lines.append(f"     acceleration coverage="
                         f"{day.get('acceleration_sample_coverage')} "
                         f"value_rows={day.get('acceleration_value_rows')} "
                         f"unavailable_rows={day.get('acceleration_unavailable_rows')} "
                         f"hours_without_any_sample="
                         f"{day.get('hours_without_any_acceleration_sample')}")
            lines.append(f"     backlog: rows_after_{day.get('backlog_window_s')}s="
                         f"{day.get('rows_arrived_after_backlog_window')} "
                         f"worst_wait_s={day.get('worst_wait_s')}")
            for silence in day.get("body_silences") or []:
                lines.append(f"     body silence: {silence.get('hours')}h "
                             f"{silence.get('start_hour')} -> {silence.get('end_hour')} "
                             f"[phone clock] (the body produced nothing)")
            hours = day.get("hours") or []
            # The rendered hours are the ones that carry the day: the latest,
            # and those with the largest waits.  The whole table stays in the
            # report for a consumer that wants it.
            rendered = sorted(
                hours,
                key=lambda hour: (-(hour.get("worst_wait_s") or 0.0),
                                  hour.get("phone_hour") or ""),
            )[:DAY_SHAPE_HOURS_RENDERED]
            rendered = sorted(rendered, key=lambda hour: hour.get("phone_hour") or "")
            for hour in rendered:
                lines.append(f"     hour {hour.get('phone_hour')}: "
                             f"produced={hour.get('produced_rows')} "
                             f"acc_value={hour.get('acceleration_value_rows')} "
                             f"acc_unavailable={hour.get('acceleration_unavailable_rows')} "
                             f"coverage={hour.get('acceleration_sample_coverage')} "
                             f"late={hour.get('arrived_after_backlog_window')} "
                             f"worst_wait_s={hour.get('worst_wait_s')}")
            if len(hours) > len(rendered):
                lines.append(f"     ({len(hours)} hours in the delivered "
                             f"history; {len(rendered)} rendered here, the rest "
                             "in the report)")
            if day.get("limits"):
                lines.append(f"     limit: {day['limits']}")
        window = node.get("window") or {}
        lines.append(f"   window rows={window.get('rows')} "
                     f"phone_sample={window.get('phone_sample_first_utc')}..{window.get('phone_sample_last_utc')} "
                     f"host_receipt={window.get('host_receipt_first_utc')}..{window.get('host_receipt_last_utc')}")
        for kind, values in (node.get("measurements") or {}).items():
            lines.append(f"   {kind}: {json.dumps(values, sort_keys=True)}")
        for kind, state in sorted((node.get("channel_availability") or {}).items()):
            lines.append(f"   {kind} availability: status={state.get('status')} "
                         f"rows={state.get('rows')} valued={state.get('valued_rows')} "
                         f"reasons={state.get('reasons')}")
        transitions = node.get("transitions") or {}
        lines.append(f"   transitions: {transitions.get('status')}")
        for item in transitions.get("transitions") or []:
            if item.get("kind") == "light-bucket":
                lines.append(f"     {item.get('at')} light {item.get('from')} -> "
                             f"{item.get('to')} lux (bucket {item.get('bucket')})")
            else:
                lines.append(f"     {item.get('at')} {item.get('channel')} "
                             f"{item.get('from')} -> {item.get('to')}")
        lines.extend(_render_continuity(node.get("continuity") or {}))
        identifiability = node.get("clock_identifiability") or {}
        if identifiability:
            lines.append(f"   clock identifiability: {identifiability.get('status')}")
            lines.append(f"     observed lag: {json.dumps(identifiability.get('observed_lag_s'), sort_keys=True)}")
            if identifiability.get("transport_delay_s"):
                lines.append(f"     transport delay: {json.dumps(identifiability.get('transport_delay_s'), sort_keys=True)}")
        for claim in node.get("claims_supported") or []:
            lines.append(f"   claim: {claim}")
        for unknown in node.get("unknowns") or []:
            lines.append(f"   UNKNOWN: {unknown}")
    fusion = report.get("cross_body_fusion") or {}
    lines.append("")
    lines.append(f"cross_body_fusion: {fusion.get('status')} - {fusion.get('reason', '')}")
    if fusion.get("current_nodes"):
        lines.append(f"  current_nodes={fusion['current_nodes']}")
    if fusion.get("stale_nodes"):
        lines.append(f"  stale_nodes={fusion['stale_nodes']}")
    return "\n".join(lines)


def _render_continuity(continuity: dict) -> list[str]:
    lines: list[str] = []
    if continuity.get("status") != "ok":
        lines.append(f"   continuity: unavailable ({continuity.get('reason')})")
        return lines
    queue = continuity.get("queue") or {}
    cadence = continuity.get("cadence") or {}
    span = continuity.get("span") or {}
    coverage = continuity.get("coverage") or {}
    lines.append(f"   continuity: rows={queue.get('rows')} "
                 f"malformed={queue.get('malformed_rows')} "
                 f"seq_contiguous={queue.get('sequence_contiguous')} "
                 f"cadence={cadence.get('interval_s')}s "
                 f"channel={cadence.get('channel')} "
                 f"span={span.get('start_phone')}..{span.get('end_phone')} "
                 f"[phone clock] ({span.get('duration_s')}s)")
    lines.append(f"     coverage: observations={coverage.get('observation_coverage')} "
                 f"[{coverage.get('observation_coverage_clock')}] "
                 f"live_delivery={coverage.get('live_delivery_coverage')} "
                 f"[{coverage.get('live_delivery_coverage_clock')}] "
                 f"link_state={coverage.get('link_state')} "
                 f"body_missing_s={coverage.get('body_missing_s')} "
                 f"link_silent_s={coverage.get('link_silent_s')}")
    anchor = continuity.get("clock_anchor") or {}
    if anchor:
        lines.append(f"     clock anchor: {anchor.get('status')} "
                     f"bounds={anchor.get('offset_bounds_s')}")
        if anchor.get("reason"):
            lines.append(f"       {anchor.get('reason')}")
    alignment = continuity.get("alignment") or {}
    if alignment:
        lines.append(f"     alignment: {alignment.get('status')} "
                     f"offset={alignment.get('offset_nominal_s')} "
                     f"uncertainty_s={alignment.get('uncertainty_s')}")
    control = continuity.get("backlog_control") or {}
    if control.get("status") == "ok":
        lines.append(f"     backlog control: gaps={control.get('gaps')} "
                     f"min_ratio={control.get('min_ratio')} "
                     f"median_ratio={control.get('median_ratio')} "
                     f"weakest={control.get('weakest', {}).get('start_utc')}.."
                     f"{control.get('weakest', {}).get('end_utc')} "
                     f"({control.get('weakest', {}).get('backlog_ratio')})")
    boots = continuity.get("device_boots") or {}
    for boot in boots.get("reboots") or []:
        lines.append(f"     device reboot: boot_estimate={boot.get('boot_estimate')} "
                     f"counter_fall_s={boot.get('counter_fall_s')} "
                     f"uptime_at_first_row_s={boot.get('uptime_at_first_row_s')} "
                     f"session={boot.get('session_after')} "
                     f"host_bracket={boot.get('host_bracket_utc')}")
        lines.append(f"       frame: {boot.get('boot_estimate_frame')}")
    for hole in continuity.get("body_holes") or []:
        lines.append(f"     body hole {hole.get('duration_s')}s "
                     f"(missing {hole.get('missing_s')}s) "
                     f"{hole.get('start_phone')} -> {hole.get('end_phone')} "
                     f"[phone clock] "
                     f"resumption={hole.get('resumption_kind')} "
                     f"link_silent_share={hole.get('link_silent_share')} "
                     f"holds_unless_clock_differs_by_s="
                     f"{hole.get('verdict_holds_unless_clock_differs_by_s')}")
        lines.append(f"       cause: {hole.get('cause')}")
        reboot = hole.get("reboot_inside")
        if reboot:
            lines.append(f"       device reboot inside: "
                         f"boot_estimate={reboot.get('boot_estimate')} "
                         f"uptime_at_first_row_s="
                         f"{reboot.get('uptime_at_first_row_s')}")
    for hole in continuity.get("link_holes") or []:
        lines.append(f"     link hole {hole.get('duration_s')}s "
                     f"{hole.get('start_utc')} -> {hole.get('end_utc')} "
                     f"attribution={hole.get('attribution')} "
                     f"samples_inside={hole.get('samples_inside')} "
                     f"expected={hole.get('expected_samples')} "
                     f"backlog_ratio={hole.get('backlog_ratio')} "
                     f"spool_drops_inside={len(hole.get('spool_drops_inside') or [])}")
        lines.append(f"       cause: {hole.get('cause')}")
    if queue.get("malformed_samples"):
        lines.append(f"     malformed samples kept: {queue.get('malformed_samples')}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--space", default="/home/mesh-home/android-body/.mishe-tauftauf",
                        help="Android site home holding space.json and body/")
    parser.add_argument("--max-age", type=float, default=300.0,
                        help="seconds a host receipt may be old and still count as current")
    parser.add_argument("--hole-threshold", type=float, default=DEFAULT_HOLE_THRESHOLD_S,
                        help="seconds of silence that count as a hole")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    space = Path(args.space)
    now = datetime.now(timezone.utc).timestamp()
    report = build_report(space, args.max_age, now, args.hole_threshold)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
