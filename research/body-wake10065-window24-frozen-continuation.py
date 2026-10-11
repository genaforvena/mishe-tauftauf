#!/usr/bin/env python3
"""Window-23 frozen-continuation row for Note3 light-stream episode 4.

Replicates the window-22 probe: 12 reads @15s of the Note3 perception state
(body/perception/note3.json, refreshed by the boundary follow daemon) plus the
freeze record, same row schema as body-wake9859-window22-frozen-continuation.
Read-only; writes only the two window-24 artifacts.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

BODY_HOME = Path("/home/mesh-home/mishe-tauftauf/.mishe-tauftauf")
SITE_HOME = Path("/home/mesh-home/android-body/.mishe-tauftauf")
NOTE3 = SITE_HOME / "body" / "perception" / "note3.json"
WATCH = BODY_HOME / "artifacts" / "body-recurrence-watch.jsonl"
STATE = BODY_HOME / "artifacts" / "body-recurrence-watch.state.json"
HISTORY = BODY_HOME / "discovery" / "space-light-freeze-history.jsonl"
OUT_JSON = BODY_HOME / "artifacts" / "body-wake10065-window-24-frozen-continuation.json"
OUT_JSONL = BODY_HOME / "artifacts" / "body-wake10065-window-24-frozen-continuation.jsonl"

ONSET_TS = 1791710641.447  # episode-4 onset 2026-10-11T09:23:26.983Z (pinned)
VALIDITY_S = 30.0
READS = 12
INTERVAL_S = 15.0
EXPECTED_IDENTITY = ("dd96004f-2f7e-497c-bbd2-6f988782f957", 126304, 126303)


def read_state():
    state = json.loads(NOTE3.read_text(encoding="utf-8"))
    mtime = NOTE3.stat().st_mtime
    light = state.get("latest", {}).get("light") or {}
    raw = light.get("raw", "")
    words = raw.split(" ")
    phone_sample = light.get("collected")
    received = light.get("received")
    sequence = light.get("sequence")
    lux = None
    if phone_sample is not None and received is not None:
        try:
            fields = dict(w.split("=", 1) for w in words[1:] if "=" in w)
            value = float(fields.get("lux", ""))
            age = time.time() - phone_sample
            lux = value if 0 <= age <= VALIDITY_S else None
        except (ValueError, IndexError):
            lux = None
    now = time.time()
    age = (now - phone_sample) if isinstance(phone_sample, (int, float)) else None
    lag = (received - phone_sample) if isinstance(received, (int, float)) and isinstance(phone_sample, (int, float)) else None
    verified = isinstance(age, float) and 0 <= age <= VALIDITY_S
    return {
        "ts": now,
        "ts_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": state.get("error") is None,
        "file_mtime": mtime,
        "session": state.get("session"),
        "cursor": state.get("cursor"),
        "sequence": sequence,
        "lux": lux,
        "light_status": "fresh" if verified else "stale",
        "reason": None,
        "last_error": state.get("error"),
        "last_poll_utc": datetime.fromtimestamp(state.get("poll_at") or now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "phone_sample_epoch_s": phone_sample,
        "phone_age_at_read_s": age,
        "receipt_lag_s": lag,
        "validity_seconds": VALIDITY_S,
        "verified_read": verified,
        "node_status": state.get("node_status"),
    }


def main():
    rows = []
    for index in range(READS):
        if index:
            time.sleep(INTERVAL_S)
        rows.append(read_state())

    identities = {(r["session"], r["cursor"], r["sequence"]) for r in rows}
    verified = sum(1 for r in rows if r["verified_read"])
    phone_ages = [r["phone_age_at_read_s"] for r in rows if r["phone_age_at_read_s"] is not None]
    lags = [r["receipt_lag_s"] for r in rows if r["receipt_lag_s"] is not None]
    ok = sum(1 for r in rows if r["ok"])
    errors = sum(1 for r in rows if r["last_error"])
    advances = sum(1 for a, b in zip(rows, rows[1:]) if b["file_mtime"] > a["file_mtime"])
    regressions = sum(1 for a, b in zip(rows, rows[1:]) if b["file_mtime"] <= a["file_mtime"])
    status_counts = {}
    for r in rows:
        key = f"{r['light_status']}|lux={r['lux']!r}"
        status_counts[key] = status_counts.get(key, 0) + 1

    watch_rows = [json.loads(l) for l in WATCH.read_text().splitlines() if l.strip()]
    stale = sum(1 for r in watch_rows if r["light_status"] == "stale")
    ver = sum(1 for r in watch_rows if r["verified_read"])
    fcc = sum(1 for r in watch_rows if r["light_status"] == "fresh-clock-conditional" and not r["verified_read"])
    clusters = 0
    in_cl = False
    for r in watch_rows:
        if r["lux"] is None:
            if not in_cl:
                clusters += 1
                in_cl = True
        else:
            in_cl = False
    gap_phones = len({r["phone_sample_epoch_s"] for r in watch_rows})

    state = json.loads(STATE.read_text())
    now = time.time()
    onset = state["open_streaks"]["light-stream-frozen|['dd96004f-2f7e-497c-bbd2-6f988782f957', 126304, 126303]"]["onset_ts"]

    result = {
        "probe": "body-wake10065-window-24-frozen-continuation",
        "purpose": "window-24 frozen-continuation row for episode 4 (12 reads @15s of the Note3 perception state, window-22 method)",
        "window_utc": [rows[0]["ts_utc"], rows[-1]["ts_utc"]],
        "interval_s": INTERVAL_S,
        "total_reads": len(rows),
        "identities": sorted([list(i) for i in identities]),
        "verified_reads": verified,
        "phone_age_mean_s": sum(phone_ages) / len(phone_ages) if phone_ages else None,
        "receipt_lag_mean_s": sum(lags) / len(lags) if lags else None,
        "transport_ok": ok,
        "transport_errors": errors,
        "writer_mtime_advances": advances,
        "writer_mtime_regressions": regressions,
        "status_counts": status_counts,
        "episode_age_at_window_end_s": now - onset,
        "episode_open": identities == {EXPECTED_IDENTITY},
        "freeze_record_final": {
            "cursor": rows[-1]["cursor"],
            "read_epoch_s": rows[-1]["phone_sample_epoch_s"],
            "reason": rows[-1]["reason"],
            "sequence": rows[-1]["sequence"],
            "session": rows[-1]["session"],
        },
        "audit_log": {
            "history_path": str(HISTORY),
            "history_exists": HISTORY.exists(),
            "history_lines": len(HISTORY.read_text().splitlines()) if HISTORY.exists() else 0,
        },
        "watch_state_after": state,
        "census": {
            "definition": "watch-series-only (pinned window-19+)",
            "frames": len(watch_rows),
            "window": [watch_rows[0]["ts_utc"], watch_rows[-1]["ts_utc"]],
            "counts": {"stale": stale, "verified": ver, "fresh-clock-conditional": fcc},
            "null_clusters_count": clusters,
            "gap_distinct_last_phone": gap_phones,
            "gap_last": watch_rows[-1]["ts_utc"],
        },
    }

    OUT_JSON.write_text(json.dumps(result, indent=2) + "\n")
    OUT_JSONL.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(json.dumps({k: v for k, v in result.items() if k != "watch_state_after"}, indent=2))


if __name__ == "__main__":
    main()
