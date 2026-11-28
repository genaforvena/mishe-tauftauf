#!/usr/bin/env python3
"""Does Wi-Fi RSSI *variability* track camera-seen household activity better than RSSI level?

Usage: python3 -I tools/rssi_motion.py --site SITE [--log PATH] [--now ISO] [--vantage redmi10|router-station]

--vantage router-station reads SITE/senses/map/router-station-redmi10.jsonl (the senses archive of
the Redmi-10 client's rows from network/router.jsonl, one row per poll, about every 5.6 min) with
MIN_RSSI_ROUTER rows per bucket; it writes rssi-motion-router-station.json.

Question (go-ahead or retire by the date in experiments.json): over 15-minute buckets,
is the jitter of the Redmi10 phone-to-AP RSSI (std, mean |step|) a better signal of
"the camera saw activity" than the RSSI level, which tools/rssi_fingerprint.py uses?

Inputs (read-only):
  SITE/spatial/work/household/activity.json  camera room buckets (15 min, per room)
  the Redmi10 observations log               source named by SITE/senses/map/rssi-table.json,
                                             or --log; ~5 s RSSI rows, accelerometer when awake
Output (overwritten each run): SITE/senses/map/rssi-motion.json and SITE/senses/map/rssi-motion-router-station.json, also printed.

Method and honesty rules:
  - Label: a room bucket is "active" when its camera activity >= that room's own p90; the
    house label is "any room active". Cameras are weak labels, not ground truth.
  - A bucket needs >= MIN_RSSI RSSI rows and a usable camera bucket (>= 2 frame pairs).
  - Score: AUC (Mann-Whitney) of each feature for the label. 0.5 is chance.
  - Null: the same AUC with the RSSI series shifted by 1..12 whole days either way, which
    keeps the daily rhythm but breaks the real coincidence. p = (1+#null>=obs)/(1+N).
  - Verdict SIGNAL needs a variance feature AUC >= 0.60, p <= 0.05 and a lead of >= 0.03 over
    the level feature. Otherwise NO_SIGNAL. Too little data, a missing input or a stale input
    is UNKNOWN. A SIGNAL here means correlation with camera activity, not presence or a room.
  - Phone motion: accelerometer rows exist only while the app is awake. Buckets without them
    are "phone_motion UNKNOWN" and counted, never assumed still.
"""
import datetime
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("rssi_text", Path(__file__).with_name("rssi_text.py"))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

BUCKET_S = 900
MIN_RSSI = 60          # of ~180 rows a 15-minute bucket should hold at 5 s cadence (Redmi10 vantage)
MIN_RSSI_ROUTER = 2    # the router-station logs one client row every ~5.6 min: 2-3 rows per bucket
ROUTER_CLIENT = "Redmi-10"
# Senses-owned copy of the Redmi-10 router rows. network/router.jsonl keeps two days (about 190 poll
# rows), which cannot reach MIN_JOINED; the copy keeps every row this client was seen, one per poll.
ROUTER_ARCHIVE = "senses/map/router-station-redmi10.jsonl"
VANTAGES = ("redmi10", "router-station")
MIN_ACCEL = 20
MIN_JOINED = 300
MIN_ACTIVE = 20
SHIFT_DAYS = [d for d in range(-12, 13) if d != 0]   # 24 shifts: smallest possible p is 0.04
AUC_MIN, P_MAX, LEAD_MIN = 0.60, 0.05, 0.03
STALE_S = 6 * 3600     # newest RSSI row older than this: the log stopped, say so
FEATURES = ("rssi_std", "rssi_step", "level_dev")
MIN_HOUR_POS, MIN_HOUR_NEG = 3, 5   # per hour-of-day stratum: active and calm buckets needed


def read_log(path, first_epoch):
    """Bucket start epoch -> {rssi: [dbm], acc: [magnitude]}; also the newest RSSI epoch."""
    buckets, newest = {}, None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            rec = rt.parse_obs_line(line)
            if rec is None or rec[0] < first_epoch:
                continue
            epoch, kind, val = rec
            b = buckets.setdefault(epoch - epoch % BUCKET_S, {"rssi": [], "acc": []})
            if kind == "rssi":
                b["rssi"].append(val)
                newest = epoch if newest is None or epoch > newest else newest
            else:
                b["acc"].append(math.sqrt(sum(c * c for c in val)))
    return buckets, newest


def read_router(path, first_epoch, client=ROUTER_CLIENT):
    """Router-station vantage: the AP's dBm for one named client, one row per poll (JSON lines).

    Same shape as read_log: bucket start epoch -> {rssi: [dbm], acc: []} plus the newest RSSI epoch.
    Rows with no usable ts or no such client are skipped, never guessed.
    """
    buckets, newest = {}, None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = rt.parse_json_line(line)
            except ValueError:
                continue
            ts = row.get("ts") if isinstance(row, dict) else None
            if not isinstance(ts, (int, float)) or not math.isfinite(ts) or ts < first_epoch:
                continue
            for cl in row.get("clients") or []:
                if not isinstance(cl, dict) or cl.get("name") != client:
                    continue
                dbm = cl.get("dbm")
                if not isinstance(dbm, (int, float)) or not math.isfinite(dbm):
                    continue
                epoch = int(ts)
                b = buckets.setdefault(epoch - epoch % BUCKET_S, {"rssi": [], "acc": []})
                b["rssi"].append(dbm)
                newest = epoch if newest is None or epoch > newest else newest
    return buckets, newest


def std(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def features(buckets, min_rssi=MIN_RSSI):
    allv = sorted(v for b in buckets.values() for v in b["rssi"])
    median = allv[len(allv) // 2] if allv else None
    out = {}
    for t, b in buckets.items():
        r = b["rssi"]
        if len(r) < min_rssi:
            continue
        mean = sum(r) / len(r)
        out[t] = {
            "rssi_std": std(r),
            "rssi_step": sum(abs(r[i + 1] - r[i]) for i in range(len(r) - 1)) / (len(r) - 1),
            "level_dev": abs(mean - median),
            "phone_acc_std": std(b["acc"]) if len(b["acc"]) >= MIN_ACCEL else None,
        }
    return out, median


def camera_buckets(doc):
    """room -> {bucket epoch: (activity, active_threshold)} from usable camera buckets."""
    out = {}
    for room, info in (doc.get("rooms") or {}).items():
        if not isinstance(info, dict):
            continue
        p90 = (info.get("activity_quantiles") or {}).get("p90")
        if not isinstance(p90, (int, float)):
            continue
        rows = {}
        for b in info.get("buckets") or []:
            act = b.get("activity")
            if not isinstance(act, (int, float)) or (b.get("frame_pairs") or 0) < 2:
                continue
            try:
                t = int(rt.parse_iso_offset(b["utc_iso"]).timestamp())
            except (KeyError, ValueError, TypeError):
                continue
            rows[t - t % BUCKET_S] = (act, p90)
        out[room] = rows
    return out


def auc(pos, neg):
    """P(pos > neg) + ties/2; None when either side is empty."""
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks, i = [0.0] * len(allv), 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    rank_pos = sum(r for r, (_, lab) in zip(ranks, allv) if lab)
    return (rank_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def score(feats, label, shift_s=0):
    """AUC per feature over buckets present in both; label maps bucket -> bool."""
    pos = {f: [] for f in FEATURES}
    neg = {f: [] for f in FEATURES}
    for t, active in label.items():
        row = feats.get(t - shift_s)
        if row is None:
            continue
        for f in FEATURES:
            (pos if active else neg)[f].append(row[f])
    n_pos, n_neg = len(pos[FEATURES[0]]), len(neg[FEATURES[0]])
    return {f: auc(pos[f], neg[f]) for f in FEATURES}, n_pos, n_neg


def stratified(feats, label, shift_s=0):
    """Mean over hours of day (UTC) of the per-hour AUC, so the daily rhythm cannot carry the score.

    A stratum counts only with MIN_HOUR_POS active and MIN_HOUR_NEG calm buckets; the result says how many
    hours were used. Shifting by whole days keeps each bucket's hour, so the day-shift null is the right one.
    Limit: a rhythm that starts inside an hour leaks into its boundary hours; hour strata cannot remove it.
    """
    pos = {h: {f: [] for f in FEATURES} for h in range(24)}
    neg = {h: {f: [] for f in FEATURES} for h in range(24)}
    for t, active in label.items():
        row = feats.get(t - shift_s)
        if row is None:
            continue
        hour = (t // 3600) % 24
        for f in FEATURES:
            (pos if active else neg)[hour][f].append(row[f])
    out = {f: [] for f in FEATURES}
    for h in range(24):
        if len(pos[h][FEATURES[0]]) < MIN_HOUR_POS or len(neg[h][FEATURES[0]]) < MIN_HOUR_NEG:
            continue
        for f in FEATURES:
            out[f].append(auc(pos[h][f], neg[h][f]))
    used = len(out[FEATURES[0]])
    return used, {f: (sum(v) / used if used else None) for f, v in out.items()}


def study(feats, label):
    obs, n_pos, n_neg = score(feats, label)
    if n_pos + n_neg < MIN_JOINED or n_pos < MIN_ACTIVE:
        return {"state": "UNKNOWN", "joined": n_pos + n_neg, "active": n_pos,
                "reason": f"need >= {MIN_JOINED} joined buckets and >= {MIN_ACTIVE} active"}
    nulls = {f: [] for f in FEATURES}
    for d in SHIFT_DAYS:
        res, _, _ = score(feats, label, d * 86400)
        for f in FEATURES:
            if res[f] is not None:
                nulls[f].append(res[f])
    p = {f: round((1 + sum(1 for x in nulls[f] if x >= obs[f])) / (1 + len(nulls[f])), 3) for f in FEATURES}
    best = max(("rssi_std", "rssi_step"), key=lambda f: obs[f])
    lead = obs[best] - obs["level_dev"]
    sig = obs[best] >= AUC_MIN and p[best] <= P_MAX and lead >= LEAD_MIN
    hours, s_obs = stratified(feats, label)
    s_nulls = {f: [] for f in FEATURES}
    for d in SHIFT_DAYS:
        _, s_res = stratified(feats, label, d * 86400)
        for f in FEATURES:
            if s_res[f] is not None:
                s_nulls[f].append(s_res[f])
    s_p = {f: round((1 + sum(1 for x in s_nulls[f] if x >= s_obs[f])) / (1 + len(s_nulls[f])), 3)
           if s_obs[f] is not None else None for f in FEATURES}
    return {"state": "SIGNAL" if sig else "NO_SIGNAL", "joined": n_pos + n_neg, "active": n_pos,
            "auc": {f: round(obs[f], 3) for f in FEATURES}, "p_shift_null": p,
            "null_auc_median": {f: round(sorted(nulls[f])[len(nulls[f]) // 2], 3) for f in FEATURES},
            "best_variance_feature": best, "lead_over_level": round(lead, 3),
            "stratified_by_hour": {"hours_used": hours,
                                   "auc": {f: round(s_obs[f], 3) if s_obs[f] is not None else None for f in FEATURES},
                                   "p_shift_null": s_p,
                                   "note": "mean over UTC hours of day of per-hour AUC; verdict above is unchanged"}}


def run(site, log, now, vantage="redmi10"):
    """The study result dict; UNKNOWN with a reason for every missing or stale input."""
    out = {"generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "schema": "rssi-motion/v1", "vantage": vantage}
    try:
        cam = rt.read_json(os.path.join(site, "spatial/work/household/activity.json"))
        rooms = camera_buckets(cam)
    except (OSError, ValueError) as exc:
        return dict(out, state="UNKNOWN", reason=f"camera activity.json unreadable: {exc}")
    starts = [t for rows in rooms.values() for t in rows]
    if not starts:
        return dict(out, state="UNKNOWN", reason="no usable camera buckets")
    if vantage not in VANTAGES:
        return dict(out, state="UNKNOWN", reason=f"unknown vantage {vantage!r}")
    min_rssi = MIN_RSSI
    if vantage == "router-station":
        min_rssi = MIN_RSSI_ROUTER
        log = log or os.path.join(site, ROUTER_ARCHIVE)
        try:
            buckets, newest = read_router(log, min(starts) - BUCKET_S)
        except OSError as exc:
            return dict(out, state="UNKNOWN", reason=f"router log unreadable: {exc}")
    else:
        if log is None:
            try:
                tbl = rt.read_json(os.path.join(site, "senses/map/rssi-table.json"))
                log = next(v["source"] for v in tbl["vantages"] if v.get("vantage") == "redmi10")
            except (OSError, ValueError, KeyError, StopIteration):
                return dict(out, state="UNKNOWN", reason="rssi-table.json names no redmi10 log source")
        try:
            buckets, newest = read_log(log, min(starts) - BUCKET_S)
        except OSError as exc:
            return dict(out, state="UNKNOWN", reason=f"observations log unreadable: {exc}")
    if newest is None:
        return dict(out, state="UNKNOWN", reason="no RSSI rows in the log inside the camera window")
    age = now.timestamp() - newest
    feats, median = features(buckets, min_rssi)
    out.update(log=log, rssi_median_dbm=median, min_rssi_rows_per_bucket=min_rssi, rssi_buckets=len(feats),
               newest_rssi_age_s=round(age), camera_buckets={r: len(v) for r, v in rooms.items()})
    still = [r["phone_acc_std"] for r in feats.values() if r["phone_acc_std"] is not None]
    out["phone_motion_known_buckets"] = len(still)
    out["phone_motion_unknown_buckets"] = len(feats) - len(still)

    house = {}
    for rows in rooms.values():
        for t, (act, p90) in rows.items():
            house[t] = house.get(t, False) or act >= p90
    out["house"] = study(feats, house)
    out["rooms"] = {r: study(feats, {t: a >= p for t, (a, p) in rows.items()}) for r, rows in rooms.items()}
    if still:
        cut = sorted(still)[len(still) // 2]
        calm = {t: r for t, r in feats.items() if r["phone_acc_std"] is not None and r["phone_acc_std"] <= cut}
        out["house_phone_still"] = dict(study(calm, house), accel_std_cut=round(cut, 4),
                                        note="buckets where the phone's own accelerometer std is at or below the median")
    out["state"] = out["house"]["state"]
    if age > STALE_S:
        out["state"] = "UNKNOWN"
        out["reason"] = f"newest RSSI row is {round(age)} s old (limit {STALE_S} s); the result is not current"
    return out


def summary(res):
    h = res.get("house") or {}
    if res["state"] == "UNKNOWN" or "auc" not in h:
        return f"rssi-motion: UNKNOWN {res.get('reason') or h.get('reason')}"
    a, p = h["auc"], h["p_shift_null"][h["best_variance_feature"]]
    return (f"rssi-motion: {res['state']} house auc step {a['rssi_step']} std {a['rssi_std']} level {a['level_dev']}, "
            f"p {p} vs day-shift null, {h['joined']} buckets, {h['active']} active")


def archive_router(site, src=None, client=ROUTER_CLIENT):
    """Append the named client's rows from network/router.jsonl to the senses archive, once per poll.

    Read-only on the network side. Rows already archived (ts at or before the newest archived ts) are
    skipped; rows without a usable dBm for the client are not kept. Returns the archive path.
    """
    src = src or os.path.join(site, "network/router.jsonl")
    dest = os.path.join(site, ROUTER_ARCHIVE)
    last = None
    if os.path.exists(dest):
        with open(dest, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ts = rt.parse_json_line(line)["ts"]
                except (ValueError, KeyError, TypeError):
                    continue
                last = ts if last is None or ts > last else last
    new = []
    with open(src, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = rt.parse_json_line(line)
            except ValueError:
                continue
            ts = row.get("ts") if isinstance(row, dict) else None
            if not isinstance(ts, (int, float)) or not math.isfinite(ts) or (last is not None and ts <= last):
                continue
            seen = [cl for cl in row.get("clients") or []
                    if isinstance(cl, dict) and cl.get("name") == client
                    and isinstance(cl.get("dbm"), (int, float)) and math.isfinite(cl.get("dbm"))]
            if seen:
                new.append(json.dumps({"ts": ts, "clients": [{"name": client, "dbm": seen[0]["dbm"]}]}))
    if new:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write("\n".join(new) + "\n")
    return dest


def write_result(site, vantage, res):
    name = "rssi-motion.json" if vantage == "redmi10" else f"rssi-motion-{vantage}.json"
    dest = os.path.join(site, "senses/map/" + name)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, dest)


def main(argv=None):
    args = rt.parse_cli(argv if argv is not None else sys.argv[1:], __doc__,
                        extra=lambda p: p.add_argument("--vantage", default="redmi10", choices=VANTAGES,
                                                       help="redmi10 (phone, default) or router-station"))
    now = rt.parse_iso(args.now) if args.now else datetime.datetime.now(datetime.timezone.utc)
    # Every run refreshes the router archive and both vantage results, so the hourly duty keeps the
    # router-station stratum filling; the exit code follows the requested vantage only.
    try:
        archive_router(args.site)
    except OSError:
        pass  # the router-station result below reads the archive and reports UNKNOWN if it is missing
    results = {}
    for vantage in VANTAGES:
        res = run(args.site, args.log if vantage == "redmi10" else None, now, vantage)
        write_result(args.site, vantage, res)
        results[vantage] = res
        print(summary(res) if vantage == "redmi10" else f"[{vantage}] " + summary(res))
    return 3 if results[args.vantage]["state"] == "UNKNOWN" else 0


if __name__ == "__main__":
    sys.exit(main())
