"""Read-only freshness and last-value reading of the Redmi10 observation stream.

The phone body appends one row per observation to a log file the host can read.
This reader only reads the tail of that file. It never writes, never starts or
signals the body, and never touches the perception unit. Each row is
``<seq> <host_epoch_s> <kind> <key=value ...>``.

Validity: the freshness of the stream is the age of its newest row. A missing,
unreadable, unparseable or stale stream is UNKNOWN, and its last values are kept
only as labelled history, never as current readings.
"""
from __future__ import annotations

import os
from pathlib import Path

from .redmi10_stream_text import decode_tail, parse_number, parse_row

SENSE_ID = "sense.body.redmi10.stream"
DEFAULT_LOG = Path("/home/mesh-home/android-body/.mishe-tauftauf/body/queues/redmi10.observations.log")
TAIL_BYTES = 65536
MAX_ROW_AGE_S = 120.0


def _tail_lines(path: Path, limit: int = TAIL_BYTES) -> list[str]:
    """Complete lines from the last ``limit`` bytes of the log."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        start = max(0, size - limit)
        handle.seek(start)
        return decode_tail(handle.read(), at_file_start=start == 0)


def _last_values(rows: list[dict]) -> dict[str, object]:
    """Newest value per quantity, each with the row epoch it came from."""
    last: dict[str, object] = {}
    for row in rows:
        kind, fields, epoch = row["kind"], row["fields"], row["epoch"]
        if kind == "light" and (lux := parse_number(fields.get("lux"))) is not None:
            last["lux"] = {"value": lux, "epoch": epoch}
        elif kind == "battery":
            level = parse_number(fields.get("level"))
            charging = fields.get("charging")
            if level is not None and charging in ("true", "false"):
                last["battery"] = {"level": level, "charging": charging == "true", "epoch": epoch}
        elif "wifi_link_rssi_dbm" in fields:
            if (rssi := parse_number(fields.get("wifi_link_rssi_dbm"))) is not None:
                last["wifi_rssi_dbm"] = {"value": rssi, "epoch": epoch}
    return last


def read_redmi10_stream(path: Path | str = DEFAULT_LOG, *, now: float) -> dict[str, object]:
    """Stream freshness plus the newest lux, battery and Wi-Fi RSSI values.

    Returns a discovery-shaped observation. UNKNOWN when the log cannot be read,
    holds no parseable row, or its newest row is older than MAX_ROW_AGE_S.
    """
    base = {"id": SENSE_ID, "kind": "read", "source": str(path)}
    try:
        lines = _tail_lines(Path(path))
    except OSError as exc:
        return {**base, "state": "unknown", "sample": f"stream log unreadable: {exc.__class__.__name__}"}
    rows = [row for row in (parse_row(line) for line in lines) if row is not None]
    if not rows:
        return {**base, "state": "unknown", "sample": "no parseable row in the stream tail"}
    newest = max(row["epoch"] for row in rows)
    age = now - newest
    last = _last_values(rows)
    if age > MAX_ROW_AGE_S or age < -MAX_ROW_AGE_S:
        return {**base, "state": "unknown",
                "sample": f"stream stale or future-dated: newest row {age:.0f}s old; last values are history only",
                "history": last, "last_row_epoch": newest}
    missing = [name for name in ("lux", "battery", "wifi_rssi_dbm") if name not in last]
    if len(missing) == 3:
        return {**base, "state": "unknown", "sample": "fresh rows carry no lux, battery or Wi-Fi value",
                "last_row_epoch": newest}
    parts = []
    if "lux" in last:
        parts.append(f"lux={last['lux']['value']:g}")
    if "battery" in last:
        battery = last["battery"]
        parts.append(f"battery={battery['level']:g} charging={'yes' if battery['charging'] else 'no'}")
    if "wifi_rssi_dbm" in last:
        parts.append(f"wifi_rssi={last['wifi_rssi_dbm']['value']:g}dBm")
    return {**base, "state": "verified",
            "sample": f"{' '.join(parts)} row_age={age:.0f}s"
                      + (f" (no current {', '.join(missing)})" if missing else ""),
            "values": last, "last_row_epoch": newest, "row_age_s": round(age, 1)}
