"""Read the linked Android Body consumer's retained Redmi10 observations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import time
from pathlib import Path

SENSE_ID = "sense.body.redmi10"
ANDROID_SESSION = "android-body"
REDMI_SOURCE = "http://127.0.0.1:8765/node/redmi10"
FRESH_SECONDS = 30.0
MAX_REGISTRY_BYTES = 65536
MAX_STATE_BYTES = 1048576
MAX_SEQUENCE = 9007199254740991


def _unknown(reason: str) -> dict[str, object]:
    return {"id": SENSE_ID, "kind": "read", "state": "unknown", "reason": reason,
            "sample": f"Redmi10 UNKNOWN: {reason}; source=linked Android Body consumer"}


def _read_json(path: Path, limit: int) -> tuple[dict[str, object] | None, str | None]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(raw) > limit:
        return None, "oversized"
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "malformed"
    if not isinstance(value, dict):
        return None, "not-an-object"
    return value, None


def _finite_number(value: object) -> float | None:
    if type(value) not in (int, float, str):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _linked_android_home(home: Path) -> tuple[Path | None, str | None]:
    registry, error = _read_json(home / "health" / "linked-sites.json", MAX_REGISTRY_BYTES)
    if error is not None:
        return None, f"linked-site-registry-{error}"
    if (type(registry.get("version")) is not int or registry.get("version") != 1
            or not isinstance(registry.get("sites"), list)):
        return None, "linked-site-registry-invalid"
    candidates = [site for site in registry["sites"]
                  if isinstance(site, dict) and site.get("session") == ANDROID_SESSION]
    if len(candidates) != 1:
        return None, "linked-android-body-site-missing-or-ambiguous"
    site_home = candidates[0].get("home")
    if not isinstance(site_home, str) or not Path(site_home).is_absolute():
        return None, "linked-android-body-home-invalid"
    return Path(site_home), None


def _row_status(
        row: object, kind: str, now: float,
) -> tuple[str, dict[str, str] | None, dict[str, object] | None]:
    if row is None:
        return "unknown/no-observation", None, None
    if not isinstance(row, dict) or row.get("kind") != kind:
        return "unknown/malformed-row", None, None
    sequence = row.get("sequence")
    received = _finite_number(row.get("received"))
    collected = _finite_number(row.get("collected"))
    fields = row.get("fields")
    if (type(sequence) is not int or not 0 < sequence <= MAX_SEQUENCE
            or received is None or collected is None or not isinstance(fields, dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in fields.items())):
        return "unknown/malformed-row", None, None
    if type(row.get("backlog_page_pending_at_receipt")) is not bool:
        return "unknown/malformed-row", None, None
    if row["backlog_page_pending_at_receipt"]:
        return "unknown/backlog", fields, row
    age = now - received
    if age < -5:
        return "unknown/clock-future", fields, row
    if age > FRESH_SECONDS:
        return "stale", fields, row
    if "age_ms" in fields:
        sensor_age = _finite_number(fields["age_ms"])
        if sensor_age is None or sensor_age < 0:
            return "unknown/sensor-age", fields, row
        if sensor_age > FRESH_SECONDS * 1000:
            return "stale", fields, row
    return "fresh-clock-conditional", fields, row




def _utc(epoch: float | None) -> str:
    if epoch is None:
        return "unknown"
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def _value(fields: dict[str, str] | None, key: str) -> float | None:
    return _finite_number(fields.get(key)) if fields is not None else None


def _auxiliary(latest: dict[str, object], key: str, now: float) -> tuple[str, str | None]:
    row = latest.get(key)
    if row is None:
        return "unknown/no-observation", None
    if not isinstance(row, dict) or row.get("kind") != key:
        return "unknown/malformed-row", None
    status, fields, _ = _row_status(row, key, now)
    reason = fields.get("reason") if fields is not None else None
    if status == "fresh-clock-conditional" and reason:
        return status, reason
    return status, reason


def read_redmi10(home: Path, now: float | None = None) -> dict[str, object]:
    """Return one bounded, receipt-fresh reading; never infer human movement."""
    current = time.time() if now is None else _finite_number(now)
    if current is None:
        return _unknown("reader-clock-invalid")
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        return _unknown(error or "linked-android-body-home-unknown")
    path = android_home / "body" / "perception" / "redmi10.json"
    state, error = _read_json(path, MAX_STATE_BYTES)
    if error is not None or state is None:
        return _unknown(f"consumer-state-{error or 'unavailable'}")
    cursor = state.get("cursor")
    session = state.get("session")
    latest = state.get("latest")
    if (type(state.get("version")) is not int or state.get("version") != 1
            or state.get("node") != "redmi10" or state.get("source") != REDMI_SOURCE
            or type(cursor) is not int or not 0 <= cursor <= MAX_SEQUENCE
            or not isinstance(session, str) or not session.strip() or not isinstance(latest, dict)):
        return _unknown("consumer-state-invalid")
    if type(state.get("has_more")) is not bool or state.get("has_more"):
        return _unknown("consumer-backlog-or-state-invalid")
    poll_at = _finite_number(state.get("poll_at"))
    if poll_at is None or current - poll_at < -5:
        return _unknown("consumer-poll-clock-unknown")
    if current - poll_at > FRESH_SECONDS:
        return _unknown("consumer-poll-stale")
    if state.get("error") is not None:
        return _unknown("consumer-poll-error")
    statuses: dict[str, str] = {}
    fields_by_kind: dict[str, dict[str, str] | None] = {}
    rows: dict[str, dict[str, object] | None] = {}
    for kind in ("battery", "light", "acceleration"):
        statuses[kind], fields_by_kind[kind], rows[kind] = _row_status(
            latest.get(kind), kind, current)

    battery = _value(fields_by_kind["battery"], "level")
    lux = _value(fields_by_kind["light"], "lux")
    axes = tuple(_value(fields_by_kind["acceleration"], axis) for axis in ("x", "y", "z"))
    magnitude = math.hypot(*axes) if all(value is not None for value in axes) else None
    if battery is None:
        statuses["battery"] = "unknown/value"
    if lux is None:
        statuses["light"] = "unknown/value"
    if battery is not None and not 0 <= battery <= 1:
        statuses["battery"] = "invalid/value"
    if lux is not None and lux < 0:
        statuses["light"] = "invalid/value"
    if magnitude is None:
        statuses["acceleration"] = "unknown/value"
    battery_fields = fields_by_kind["battery"]
    charging = battery_fields.get("charging") if battery_fields is not None else None
    if charging not in {"true", "false"}:
        charging = "unknown"

    acceleration_row = rows["acceleration"]
    phone_time = _finite_number(acceleration_row.get("collected")) if acceleration_row else None
    receipt_time = _finite_number(acceleration_row.get("received")) if acceleration_row else None
    channel_provenance = {
        kind: {"sequence": row["sequence"],
               "phone_sample_epoch_s": row["collected"],
               "consumer_receipt_epoch_s": row["received"]}
        for kind, row in rows.items() if row is not None
    }
    battery_text = (f"{battery:.2f}{'+' if charging == 'true' else ''}"
                    if statuses["battery"] == "fresh-clock-conditional" and battery is not None
                    else statuses["battery"])
    light_text = (f"{lux:g}lux"
                  if statuses["light"] == "fresh-clock-conditional" and lux is not None
                  else statuses["light"])
    if statuses["acceleration"] == "fresh-clock-conditional" and magnitude is not None:
        acceleration_text = f"{magnitude:.2f}m/s² incl-g"
    elif statuses["acceleration"] == "stale":
        acceleration_text = "motion-leg-absent"
    else:
        acceleration_text = statuses["acceleration"]
    # Cursor progress remains in structured provenance/details, not the
    # observation summary: cursor-only advances must not re-wake the mind.
    sample = (f"Redmi10 sid={session} b={battery_text} L={light_text} "
              f"|a|={acceleration_text}")
    status = ("verified" if all(value == "fresh-clock-conditional"
                                for value in statuses.values()) else "unknown")

    wifi_status, wifi_reason = _auxiliary(latest, "wifi_link_rssi_dbm", current)
    ble_status, ble_reason = _auxiliary(latest, "ble_scan", current)
    wifi_text = (f"Wi-Fi {wifi_reason}" if wifi_status == "fresh-clock-conditional" and wifi_reason
                 else f"Wi-Fi {wifi_status}")
    ble_text = (f"BLE last scan error {ble_reason} (stale)"
                if ble_status == "stale" and ble_reason
                else f"BLE scan {ble_reason}" if ble_reason
                else f"BLE scan {ble_status}")
    transition = state.get("last_light_transition")
    if not isinstance(transition, dict):
        transition = None
    else:
        needed = {"kind", "bucket_from", "bucket_to", "claim", "freshness_at_detection",
                  "validity_seconds", "from", "to"}
        if not needed.issubset(transition):
            transition = None
        else:
            try:
                if (transition.get("kind") != "measured_light_bucket_transition"
                        or transition.get("claim") != "historical-measured-light-change-only"
                        or not isinstance(transition.get("bucket_from"), int)
                        or not isinstance(transition.get("bucket_to"), int)
                        or not isinstance(transition.get("validity_seconds"), int)
                        or not isinstance(transition.get("from"), dict)
                        or not isinstance(transition.get("to"), dict)):
                    transition = None
            except (TypeError, ValueError):
                transition = None
    transition_text = ""
    if transition is not None:
        transition_text = (" measured-light-transition="
                           f"{transition['bucket_from']}->{transition['bucket_to']}"
                           f" validity={transition['validity_seconds']}s"
                           f" freshness={transition['freshness_at_detection']};")
    details = (f"source={REDMI_SOURCE} session={session} cursor={cursor}; "
               f"acceleration phone_sample={_utc(phone_time)} consumer_receipt={_utc(receipt_time)}; "
               f"host-receipt freshness conditional; {wifi_text}; {ble_text};{transition_text} "
               "phone/host clock split unknown; acceleration includes gravity and is not person-movement evidence.")
    return {"id": SENSE_ID, "kind": "read", "state": status,
            "reason": "fresh-clock-conditional" if status == "verified" else "channel-not-current",
            "sample": sample, "source": REDMI_SOURCE, "cursor": cursor,
            "session": session,
            "channel_provenance": channel_provenance,
            "light_transition": transition,            "acceleration_phone_sample_epoch_s": phone_time,
            "acceleration_consumer_receipt_epoch_s": receipt_time,
            "channels": statuses, "details": details}

LIGHT_SENSE_ID = "sense.body.redmi10.light"

def read_redmi10_light(home: Path, now: float | None = None) -> dict[str, object]:
    """Return the fresh carried-device light reading even when motion is absent."""
    base = read_redmi10(home, now)
    if not isinstance(base, dict) or base.get("id") != SENSE_ID:
        return {"id": LIGHT_SENSE_ID, "kind": "read", "state": "unknown",
                "reason": "composite-reader-unavailable",
                "sample": "Redmi10 light UNKNOWN: reader unavailable",
                "source": REDMI_SOURCE}
    channels = base.get("channels") if isinstance(base.get("channels"), dict) else {}
    light_status = channels.get("light", "stale")
    state = "verified" if light_status == "fresh-clock-conditional" else "unknown"
    reason = "fresh-clock-conditional" if state == "verified" else str(base.get("reason", "channel-not-current"))
    sample = str(base.get("sample", "Redmi10 light unknown"))
    return {"id": LIGHT_SENSE_ID, "kind": "read", "state": state, "reason": reason,
            "sample": sample, "source": REDMI_SOURCE,
            "cursor": base.get("cursor"), "session": base.get("session"),
            "channels": {"light": light_status},
            "light_transition": base.get("light_transition"),
            "details": ("Redmi10 light sense inherits Redmi consumer provenance; "
                        "acceleration may be unavailable without hiding carried light context.")}


GAP_SENSE_ID = "sense.body.redmi10.delivery-gaps"
GAP_LOG_NAME = "class-c-delivery-gap-detectors.jsonl"
EXCURSION_LOG_NAME = "excursion-log-redmi10.txt"
#: Both logs are bounded by the producer at 128 lines; the caps leave room for
#: the widest line each format can carry, so a full log is never read as
#: oversized and silently loses the reading it holds.
MAX_GAP_LOG_BYTES = 65536
MAX_EXCURSION_LOG_BYTES = 262144
#: The recurrence window: how far back the reported halt count reaches.
GAP_WINDOW_SECONDS = 86400.0

DENSE_SENSE_ID = "sense.body.redmi10.dense-baseline"
#: The carried-body rows the Senses pane re-reads at render time, so their
#: freshness is the render's, never the discovery scan's.
#: The dense baseline is deliberately excluded: it only updates on the
#: scan renewal, so the pane renders its scan row under the scan freshness
#: gate, keeping the producer's own stale/unknown verdict visible.
BODY_SENSE_IDS = frozenset({SENSE_ID, LIGHT_SENSE_ID, GAP_SENSE_ID})


def _gaps_unknown(reason: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": GAP_SENSE_ID, "kind": "read", "state": "unknown", "reason": reason,
        "sample": f"Redmi10 delivery gaps UNKNOWN: {reason}", "source": REDMI_SOURCE}
    row.update(extra)
    return row


def _read_text(path: Path, limit: int) -> tuple[str | None, str | None]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(raw) > limit:
        return None, "oversized"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "malformed"


def _gap_records(text: str) -> tuple[list[dict[str, object]] | None, int]:
    """Parse the consumer's class-C halt records; ``None`` when any line is bad.

    A line that does not carry the published shape is counted, never guessed at:
    the count would be a lower bound, so the caller reports UNKNOWN instead of a
    number a reader could take for the whole history.
    """
    records: list[dict[str, object]] = []
    bad = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        detected = record.get("detected_at") if isinstance(record, dict) else None
        received = _finite_number(record.get("last_received_epoch_s")) if isinstance(record, dict) else None
        try:
            stamp = datetime.fromisoformat(detected) if isinstance(detected, str) else None
        except ValueError:
            stamp = None
        if (not isinstance(record, dict)
                or record.get("kind") != "class-c-delivery-gap"
                or type(record.get("cursor")) is not int
                or not 0 <= record["cursor"] <= MAX_SEQUENCE
                or not isinstance(record.get("session"), str) or not record["session"].strip()
                or received is None
                or type(record.get("host_stopped")) not in (bool, type(None))
                or stamp is None or stamp.tzinfo is None):
            bad += 1
            continue
        records.append({"detected_epoch_s": stamp.timestamp(), "last_received_epoch_s": received,
                        "cursor": record["cursor"], "session": record["session"],
                        "host_stopped": record["host_stopped"]})
    return (None, bad) if bad else (records, 0)


def _worst_batch_delay(text: str) -> float | None:
    """Widest receipt-minus-collection span in the retained excursion lines, in seconds.

    That span is how long a halted batch waited before the consumer received it:
    the flushed rows keep their phone sample times but not their receipt times.
    """
    worst: float | None = None
    for line in text.splitlines():
        if not line.startswith("excursion emitted=") or " node=redmi10 " not in line:
            continue
        for token in line.split():
            if not token.startswith("acquisition_span_ms="):
                continue
            high = _finite_number(token.partition("=")[2].partition(",")[2])
            if high is None or high < 0:
                return None
            if worst is None or high > worst:
                worst = high
    return None if worst is None else worst / 1000.0


def _lag_text(seconds: float) -> str:
    """A compact floor-rounded duration; the exact seconds stay structured.

    The pane clips a long sample at 120 cells, and a corrupted log can carry an
    absurd finite span, so the rendered token is bounded whatever the input is.
    """
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    minutes = whole // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h{minutes % 60}m"
    return f"{hours // 24}d{hours % 24}h"


def read_redmi10_delivery_gaps(home: Path, now: float | None = None) -> dict[str, object]:
    """Count the consumer's detected Redmi delivery halts over the last day.

    A halt is the consumer's class-C verdict: no new record for its window while
    polls keep succeeding, with no armed class-A or class-B cause. It is the
    phenomenon that leaves receipt ages in the retained window far behind the
    phone sample times, so a reader needs it before treating a Redmi window as
    contemporaneous. Counts are by detection time and say nothing about how long
    each halt lasted or why it started.
    """
    current = time.time() if now is None else _finite_number(now)
    if current is None:
        return _gaps_unknown("reader-clock-invalid")
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        return _gaps_unknown(error or "linked-android-body-home-unknown")
    perception = android_home / "body" / "perception"
    text, error = _read_text(perception / GAP_LOG_NAME, MAX_GAP_LOG_BYTES)
    if error is not None or text is None:
        return _gaps_unknown(f"gap-log-{error or 'unreadable'}")
    records, bad = _gap_records(text)
    if records is None:
        return _gaps_unknown("gap-log-malformed", malformed=bad)
    if not records:
        return _gaps_unknown("gap-log-empty")
    records.sort(key=lambda record: record["detected_epoch_s"])
    if any(record["detected_epoch_s"] > current + 5 for record in records):
        return _gaps_unknown("gap-log-clock-future")
    window = [record for record in records
              if current - record["detected_epoch_s"] <= GAP_WINDOW_SECONDS]
    last = records[-1]
    excursion, excursion_error = _read_text(perception / EXCURSION_LOG_NAME, MAX_EXCURSION_LOG_BYTES)
    delay = _worst_batch_delay(excursion) if excursion is not None else None
    delay_text = _lag_text(delay) if delay is not None else (
        f"unknown-{excursion_error}" if excursion_error is not None else "unknown")
    delay_exact = f"{delay:g}s" if delay is not None else "unknown"
    stopped = last["host_stopped"]
    stopped_text = "unknown" if stopped is None else ("true" if stopped else "false")
    sample = (f"gaps-24h={len(window)} last-onset={_utc(last['last_received_epoch_s'])} "
              f"host-stopped={stopped_text} max-receipt-lag={delay_text}")
    details = (f"source=linked android-body body/perception/{GAP_LOG_NAME}; "
               f"window={GAP_WINDOW_SECONDS:.0f}s since={_utc(records[0]['detected_epoch_s'])} "
               f"records={len(records)} malformed={bad}; "
               "detector=consumer class-C (delivery halted while polls succeed); "
               "host-stopped is the detector's own adb read, not a delivery verdict; "
               f"max-receipt-lag={delay_text} ({delay_exact}) is the widest "
               "receipt-minus-collection span over the "
               f"retained {EXCURSION_LOG_NAME} lines, so a flushed batch keeps its phone "
               "sample times but not its receipt times; halt length and cause are not "
               "derivable from this log.")
    return {"id": GAP_SENSE_ID, "kind": "read", "state": "verified", "reason": "gap-log-current",
            "sample": sample, "source": REDMI_SOURCE,
            "gaps_24h": len(window), "gap_records": len(records),
            "last_onset_epoch_s": last["last_received_epoch_s"],
            "last_gap_session": last["session"], "last_gap_cursor": last["cursor"],
            "host_stopped": stopped, "max_receipt_lag_s": delay,
            "window_seconds": GAP_WINDOW_SECONDS, "details": details}

#: The consumer's dense observation queue file (append-only, ~5 s cadence).
DENSE_QUEUE_NAME = "redmi10.observations.log"
#: Tail bytes read from the dense queue; at ~101 bytes/line this yields ~2500
#: lines (~3.5 h at the observed 5 s cadence), bounding the analysis window.
DENSE_TAIL_BYTES = 262144
#: Rows retained for the compact summary; at 5 s cadence this is a ~2.8 h window.
DENSE_WINDOW_ROWS = 2000
#: Freshness gate for the dense-baseline verdict; matches ``FRESH_SECONDS``.
DENSE_FRESH_SECONDS = 30.0


def _read_tail(path: Path, limit: int) -> tuple[str | None, str | None]:
    """Read the last ``limit`` bytes of a file, or None with a reason."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            raw = stream.read(limit)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "malformed"


def _dense_unknown(reason: str) -> dict[str, object]:
    return {"id": DENSE_SENSE_ID, "kind": "read", "state": "unknown", "reason": reason,
            "sample": f"Redmi10 dense-baseline UNKNOWN: {reason}; source=linked Android Body consumer"}


def _dense_light_class(lux: float) -> str:
    if lux <= 0:
        return "dark"
    if lux < 50:
        return "dim"
    if lux < 1000:
        return "bright"
    return "very-bright"


def read_redmi10_dense_baseline(home: Path, now: float | None = None) -> dict[str, object]:
    """Compact carried-device context from the consumer's dense observation queue.

    Reads the tail of the consumer's dense queue (append-only, ~5 s cadence) and
    returns a bounded summary: last light class, charging state, acceleration
    availability, and delivery break. The verdict is ``verified`` when the last
    row is fresh (< ``DENSE_FRESH_SECONDS``), ``stale`` when it is older, and
    ``unknown`` when the queue is unreadable. Deltas come from a fixed-cadence
    re-read (the discovery scan renewal), not from the scan corpus.
    """
    current = time.time() if now is None else _finite_number(now)
    if current is None:
        return _dense_unknown("reader-clock-invalid")
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        return _dense_unknown(error or "linked-android-body-home-unknown")
    queue = android_home / "body" / "queues" / DENSE_QUEUE_NAME
    text, error = _read_tail(queue, DENSE_TAIL_BYTES)
    if error is not None or text is None:
        return _dense_unknown(f"dense-queue-{error or 'unreadable'}")
    lines = text.splitlines()
    if not lines:
        return _dense_unknown("dense-queue-empty")
    last_light_lux: float | None = None
    last_light_epoch: int | None = None
    last_charging: bool | None = None
    last_battery_epoch: int | None = None
    accel_count = 0
    motion_count = 0
    total_count = 0
    first_epoch: int | None = None
    last_epoch: int | None = None
    for line in lines[-DENSE_WINDOW_ROWS:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            epoch = int(parts[1])
        except ValueError:
            continue
        kind = parts[2]
        total_count += 1
        if first_epoch is None:
            first_epoch = epoch
        last_epoch = epoch
        if kind == "light":
            for part in parts[3:]:
                if part.startswith("lux="):
                    try:
                        last_light_lux = float(part[4:])
                        last_light_epoch = epoch
                    except ValueError:
                        pass
                    break
        elif kind == "battery":
            for part in parts[3:]:
                if part.startswith("charging="):
                    val = part[9:]
                    if val in ("true", "false"):
                        last_charging = val == "true"
                        last_battery_epoch = epoch
                    break
        elif kind == "acceleration":
            accel_count += 1
            # An acceleration row that carries no axis values is a delivery
            # gap, not a motion observation: counting it as coverage made the
            # reported rate describe the poll cadence, not the motion
            # channel. Only a row carrying all three axes is an observation.
            if all(any(part.startswith(f"{axis}=") for part in parts[3:])
                   for axis in ("x", "y", "z")):
                motion_count += 1
    if total_count == 0 or last_epoch is None:
        return _dense_unknown("dense-queue-empty")
    break_seconds = current - last_epoch
    state = "verified" if break_seconds < DENSE_FRESH_SECONDS else "stale"
    window_seconds = (last_epoch - first_epoch) if first_epoch is not None else 0
    accel_ratio = accel_count / total_count if total_count > 0 else 0.0
    motion_ratio = motion_count / total_count if total_count > 0 else 0.0
    light_class = _dense_light_class(last_light_lux) if last_light_lux is not None else "unknown"
    light_text = f"{last_light_lux:g}lux {light_class}" if last_light_lux is not None else "unknown"
    charging_text = ("yes" if last_charging else "no") if last_charging is not None else "unknown"
    sample = (f"L={light_text} charging={charging_text} "
              f"motion={motion_ratio:.0%} break={break_seconds:.0f}s "
              f"window={window_seconds}s rows={total_count}")
    details = (f"source=linked android-body body/queues/{DENSE_QUEUE_NAME}; "
               f"tail-bytes={DENSE_TAIL_BYTES} window-rows={total_count}; "
               f"last-light={light_text} at epoch {last_light_epoch}; "
               f"last-charging={charging_text} at epoch {last_battery_epoch}; "
               f"motion-coverage={motion_ratio:.1%} "
               f"(rows carrying x/y/z values); "
               f"delivery-coverage={accel_ratio:.1%} "
               f"(acceleration rows present, values or not); "
               f"break={break_seconds:.1f}s; "
               "phone/host clock split unknown; acceleration includes gravity "
               "and is not person-movement evidence.")
    return {"id": DENSE_SENSE_ID, "kind": "read", "state": state,
            "reason": "dense-queue-current" if state == "verified" else "dense-queue-stale",
            "sample": sample, "source": REDMI_SOURCE,
            "last_light_lux": last_light_lux, "last_light_class": light_class,
            "last_light_epoch_s": last_light_epoch,
            "charging": last_charging, "charging_epoch_s": last_battery_epoch,
            "accel_ratio": accel_ratio, "motion_ratio": motion_ratio,
            "motion_count": motion_count, "accel_count": accel_count,
            "break_seconds": break_seconds,
            "window_seconds": window_seconds, "row_count": total_count,
            "details": details}
