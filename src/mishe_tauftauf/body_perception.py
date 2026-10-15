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
    acceleration_text = (f"{magnitude:.2f}m/s² incl-g"
                         if statuses["acceleration"] == "fresh-clock-conditional" and magnitude is not None
                         else statuses["acceleration"])
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
