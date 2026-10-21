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
from datetime import datetime, timezone
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

#: The clock of the carried body cannot be recovered from the delivered lane.
#: This is stated for every node, whatever else the reading holds.
_CLOCK_NOT_IDENTIFIABLE = (
    "the phone clock offset is not recoverable from the delivered lane: a "
    "constant transport delay and a constant clock offset produce identical "
    "rows, so the phone's own timestamps are only as good as an unverified "
    "clock")


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


def _read_queue(path: Path) -> dict:
    """Read the delivered observation queue into ordered samples.

    Malformed lines are counted and kept as evidence: a queue that ends in
    unreadable rows is a fact about the delivery, not noise to be dropped.
    """
    rows: list[tuple[int, int, str]] = []
    malformed: list[str] = []
    malformed_rows = 0
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
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3)))
    return {
        "status": "ok",
        "path": str(path),
        "rows": rows,
        "malformed_rows": malformed_rows,
        "malformed_samples": malformed,
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


def _continuity(body: Path, node: str, threshold: float, clock: dict) -> dict:
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

    body_holes: list[dict] = []
    for start, end in _holes(series, cadence, threshold):
        resumption_kind = first_row_at.get(end)
        share, margin = link_silent_share(start, end)
        expected = (end - start) / cadence
        if share is None:
            cause = ("the body produced no observations across this interval of "
                     "its own clock; the host journal cannot say whether it was "
                     "receiving then, so whether the link was live is UNKNOWN")
        elif share > 0.0:
            cause = ("not separable: the body produced no observations across "
                     "this interval and the host received nothing across "
                     f"{round(share * 100, 1)}% of it, so the delivered lane "
                     "cannot separate a body-side pause from a link-side outage")
        else:
            cause = ("body-side: the body produced no observations across this "
                     "interval of its own clock while the host kept receiving")
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
            "cause": cause,
        })

    link_holes: list[dict] = []
    for index in range(len(posts) - 1):
        start = float(posts[index])
        end = float(posts[index + 1])
        if end - start <= threshold:
            continue
        produced = samples_inside(start, end)
        expected = (end - start) / cadence
        tolerance = max(2.0, 0.05 * expected)
        if produced >= expected - tolerance:
            cause = ("link-side: the body kept producing observations at its "
                     f"cadence ({produced} samples, about {round(expected)} "
                     "expected) and they were delivered afterwards")
        elif produced > 0:
            cause = ("partial: the body produced only "
                     f"{produced} samples where about {round(expected)} were "
                     "expected at its cadence, so the body faltered as well as "
                     "the link")
        else:
            cause = ("not separable: the host received nothing and the body "
                     "produced nothing across the same interval")
        link_holes.append({
            "start_utc": _iso(start),
            "end_utc": _iso(end),
            "duration_s": round(end - start, 3),
            "samples_inside": produced,
            "expected_samples": round(expected),
            "rows_sampled_inside": sum(1 for _, epoch, _ in rows
                                       if start <= epoch <= end),
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
    if state is None:
        reading["current_window"] = False
        reading["clock_identifiability"] = _clock_identifiability(
            None, clock.get("status"), clock.get("offset_nominal_s"),
            clock.get("offset_min_s"), clock.get("offset_max_s"))
        reading["unknowns"] = ["no delivered perception state file for this node"]
        if clock.get("status") != "ok":
            reading["unknowns"].append(_CLOCK_NOT_IDENTIFIABLE)
        reading["continuity"] = _continuity(body, name, threshold, clock)
        _attach_clock_anchor(reading, clock)
        reading["unknowns"].extend(_continuity_unknowns(reading["continuity"]))
        return reading

    history = state.get("history")
    history = history if isinstance(history, list) else []
    span = _row_span(history)
    reading["window"] = span
    reading["cursor"] = state.get("cursor")

    receipts = [row.get("received") for row in history
                if isinstance(row.get("received"), (int, float))]
    newest = max(receipts) if receipts else None
    age = (now - newest) if newest is not None else None
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
    reading["continuity"] = _continuity(body, name, threshold, clock)
    reading["clock_identifiability"] = _clock_identifiability(
        _delivery_lag(history), clock.get("status"), clock.get("offset_nominal_s"),
        clock.get("offset_min_s"), clock.get("offset_max_s"))
    _attach_clock_anchor(reading, clock)

    # --- gates ----------------------------------------------------------
    unknowns: list[str] = []
    if clock.get("status") != "ok":
        reason = reading.get("clock_reason")
        detail = f": {reason}" if reason else ""
        unknowns.append(
            "absolute phone time is UNKNOWN for this node (no measured "
            f"phone/host clock offset{detail}): only host receipt anchors a "
            "reading here")
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
    if clock.get("status") != "ok":
        unknowns.append(_CLOCK_NOT_IDENTIFIABLE)
    reading["unknowns"] = unknowns
    claims = (["a current, host-anchored reading of this body's own sensed channels"]
              if current else [])
    if clock.get("status") == "ok":
        claims.append("the body's own timestamps are anchored to the host "
                      "timeline within the measured clock offset, so its "
                      "observations can be placed on the host's clock")
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
              if (hole.get("link_silent_share") or 0.0) > 0.0]
    if shared:
        total = sum(hole["duration_s"] for hole in shared)
        unknowns.append(
            f"the body produced no observations across {len(shared)} interval(s) "
            f"totalling {round(total, 1)} s, and the host received nothing "
            "across the same intervals: the delivered lane cannot separate a "
            "body-side pause from a link-side outage")
    recovered = [hole for hole in link_holes
                 if str(hole.get("cause", "")).startswith("link-side")]
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
        lines.append(f"   current_window={node.get('current_window')} "
                     f"host_receipt_age_s={node.get('host_receipt_age_seconds')} "
                     f"cursor={node.get('cursor')}")
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
    for hole in continuity.get("link_holes") or []:
        lines.append(f"     link hole {hole.get('duration_s')}s "
                     f"{hole.get('start_utc')} -> {hole.get('end_utc')} "
                     f"samples_inside={hole.get('samples_inside')} "
                     f"expected={hole.get('expected_samples')}")
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
