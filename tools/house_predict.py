#!/usr/bin/env python3
"""House prediction log: forecasts about the house, written BEFORE the hour happens, scored after.

Reads the hourly feature store (features/house-hourly.jsonl, tools/household_features.py) and keeps
two plain baselines that every later net must beat:

  persist  the last complete hour's value ("nothing changes")
  how      the median of the same hour of the week (hour-of-day while there are <3 weeks of history)
  damp     the first net: persist + a*(how - persist), a least-squares pull fitted on the last 168 pairs and
           clipped to 0..1. It is judged against the BETTER baseline on the hours where all three forecast;
           it counts only with MIN_PAIRS such hours. A net that cannot forecast an hour never blocks the baselines.

Each run (hour c = the current UTC hour) writes, for every target, net and horizon h in {1, 24},
one forecast for the hour that starts at c + h hours: strictly in the future, built only from complete
hours before c. Later runs score a forecast when that hour's row reaches the store.

Files (SITE/features/):
  predictions.jsonl         append-only; every line carries `prev`, the sha256 of the previous line, so
                            an edited or removed line is detected (`chain` in the score file)
  predictions.state.json    derived: pending forecasts, last target per series, running aggregates;
                            rebuildable from the log
  predictions-score.json    derived: the scoreboard the house view prints (live, and a walk-forward replay)

Rules: a forecast is never made from missing data; an hour with no observation is `unobserved`, never an
error of zero; the replay is retrospective and labelled so, it is not evidence of live skill; the log is
never rewritten. Units are rooms and devices, never people.

usage: house_predict.py --site SITE [--now EPOCH]
Exit codes (duty runner): 0 fresh, 4 UNKNOWN (no feature store, or its newest hour is over 4 h old:
no forecast was made), 1 crash. One JSON line on stdout becomes the duty detail.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

HOUR = 3600
HORIZONS = (1, 24)
NETS = ("persist", "how", "damp")
BASELINES = ("persist", "how")
FIT_PAIRS = 168                     # damp: its coefficient is fitted on the last 168 walk-forward pairs
STALE_S = 4 * HOUR
UNOBSERVED_AFTER_S = 6 * HOUR
MIN_PAIRS = 24
HOW_KEEP = 8
RECENT = 48
SURPRISE_Z = 3.0

TARGETS = {   # slug -> (label, section, room, field)
    "lr_light": ("living-room light", "camera", "living room", "luma_mean"),
    "lr_motion": ("living-room motion", "camera", "living room", "motion"),
    "br_light": ("bedroom light", "camera", "bedroom", "luma_mean"),
    "br_motion": ("bedroom motion", "camera", "bedroom", "motion"),
    "lr_sound": ("living-room sound level", "audio", "livingroom", "rms_p50"),
    "lr_loud": ("living-room loud moments", "audio", "livingroom", "rms_p90"),
    "lr_quiet": ("living-room quiet share", "audio", "livingroom", "silent_frac"),
}


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def hour_start(t: float) -> int:
    return int(t // HOUR) * HOUR


def value_of(row: dict, slug: str):
    """The target's number in one store row, or None. Absence stays absence."""
    _, section, room, field = TARGETS[slug]
    block = row.get(section)
    feat = block.get(room) if isinstance(block, dict) else None
    if not isinstance(feat, dict):
        return None
    if field == "silent_frac":
        status, n = feat.get("status"), feat.get("n")
        if isinstance(status, dict) and finite(n) and n > 0 and finite(status.get("SILENT_OR_DROPPED", 0)):
            return status.get("SILENT_OR_DROPPED", 0) / n
        return None
    v = feat.get(field)
    return float(v) if finite(v) else None


def load_store(path: Path):
    """Store rows by hour. Torn or malformed lines are skipped (the features tool counts them)."""
    rows = {}
    if not path.is_file():
        return rows
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict) and finite(row.get("t")):
                rows[int(row["t"])] = row
    return rows


class Series:
    """One target's values by hour, with bisect lookups for the hour-of-week median."""

    def __init__(self, values: dict):
        self.values = values
        self.by_hw, self.by_hod = {}, {}
        for t in sorted(values):
            g = time.gmtime(t)
            self.by_hw.setdefault((g.tm_wday, g.tm_hour), []).append(t)
            self.by_hod.setdefault(g.tm_hour, []).append(t)

    def _median(self, times, c):
        k = bisect.bisect_left(times, c)                  # only hours strictly before c
        picked = times[max(0, k - HOW_KEEP):k]
        if len(picked) < 3:
            return None, len(picked)
        return statistics.median(self.values[t] for t in picked), len(picked)

    def _how(self, target_t: int, c: int):
        """(value, basis) of the same-hour baseline for target_t, using only hours before c. None: too little history."""
        g = time.gmtime(target_t)
        v, n = self._median(self.by_hw.get((g.tm_wday, g.tm_hour), []), c)
        if v is not None:
            return v, f"hour-of-week n={n}"
        v, n = self._median(self.by_hod.get(g.tm_hour, []), c)
        if v is not None:
            return v, f"hour-of-day n={n}"
        return None

    def _damp_coef(self, c: int, h: int):
        """Least-squares a for damped persistence, from the last FIT_PAIRS origins whose target is observed before c.
        Origin u forecasts u+h as persist_u + a*(how_u - persist_u), with how_u built from hours before u only."""
        num = den = 0.0
        pairs = 0
        for j in range(1, FIT_PAIRS + 1):
            target = c - j * HOUR
            u = target - h * HOUR
            y, p = self.values.get(target), self.values.get(u - HOUR)
            if y is None or p is None:
                continue
            s = self._how(target, u)
            if s is None:
                continue
            d, e = s[0] - p, y - p
            num += d * e
            den += d * d
            pairs += 1
        if pairs < MIN_PAIRS or den <= 0:
            return None
        return min(1.0, max(0.0, num / den))

    def forecast(self, net: str, c: int, h: int):
        """(value, basis) for the hour starting at c + h hours, from complete hours before c. None: no forecast."""
        target_t = c + h * HOUR
        if net == "persist":
            v = self.values.get(c - HOUR)
            return (v, "last hour") if v is not None else None
        if net == "how":
            return self._how(target_t, c)
        # damp: last value + a * (same-hour baseline - last value)
        p = self.values.get(c - HOUR)
        s = self._how(target_t, c)
        a = self._damp_coef(c, h) if p is not None and s is not None else None
        if a is None:
            return None
        return p + a * (s[0] - p), f"damped a={a:.2f}"


def sha(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def log_tail_hash(path: Path) -> str:
    if not path.is_file() or path.stat().st_size == 0:
        return ""
    with open(path, "rb") as fh:
        fh.seek(max(0, path.stat().st_size - 65536))
        lines = [ln for ln in fh.read().split(b"\n") if ln.strip()]
    return sha(lines[-1]) if lines else ""


def append_log(path: Path, records: list) -> None:
    prev = log_tail_hash(path)
    out = []
    for rec in records:
        rec = dict(rec, prev=prev)
        line = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()
        prev = sha(line)
        out.append(line)
    if out:
        with open(path, "ab") as fh:
            fh.write(b"\n".join(out) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())


def verify_chain(path: Path) -> dict:
    """Stream the log and check every `prev`. {"state": "ok"|"broken"|"unknown", "lines": n, "bad_line": k}."""
    if not path.is_file():
        return {"state": "unknown", "lines": 0, "reason": "no log yet"}
    prev, n = "", 0
    with open(path, "rb") as fh:
        for raw in fh:
            raw = raw.rstrip(b"\n")
            if not raw.strip():
                continue
            n += 1
            try:
                rec = json.loads(raw)
            except ValueError:
                return {"state": "broken", "lines": n, "bad_line": n, "reason": "not JSON"}
            if not isinstance(rec, dict) or rec.get("prev") != prev:
                return {"state": "broken", "lines": n, "bad_line": n, "reason": "previous-line hash differs"}
            prev = sha(raw)
    return {"state": "ok", "lines": n}


def fresh_state() -> dict:
    return {"v": 1, "last": {}, "pending": {}, "agg": {}, "surprises": [],
            "counts": {"forecasts": 0, "scored": 0, "unobserved": 0}, "first_live": None}


def agg_slot(state: dict, slug: str, h: int) -> dict:
    """Per target and horizon. `pair` is the baselines' pair (persist and how present); `tri` is the hours where
    every net forecast, so a net's edge is measured on the same hours as the better baseline. Old state slots that
    lack a net (written before it existed) get it here, so a new net never blocks the baselines."""
    slot = state["agg"].setdefault(f"{slug}|{h}", {"nets": {}, "pair": {"n": 0, "abs": {}},
                                                   "tri": {"n": 0, "abs": {}}, "recent": []})
    for n in NETS:
        slot["nets"].setdefault(n, {"n": 0, "abs": 0.0})
        slot["pair"]["abs"].setdefault(n, 0.0)
        slot["tri"]["abs"].setdefault(n, 0.0)
    return slot


def apply_scored(state: dict, scored: list) -> None:
    """Fold scored outcomes (dicts with net, slug, h, t, abs_err, forecast, obs) into the aggregates."""
    groups = {}
    for s in scored:
        slot = agg_slot(state, s["slug"], s["h"])
        net = slot["nets"][s["net"]]          # agg_slot guarantees the key
        if s["net"] == "how" and net["n"] >= MIN_PAIRS and net["abs"] > 0:
            z = s["abs_err"] / (net["abs"] / net["n"])
            if z >= SURPRISE_Z:
                state["surprises"].append({"t": s["t"], "slug": s["slug"], "h": s["h"], "obs": s["obs"],
                                           "forecast": s["forecast"], "z": round(z, 1)})
                state["surprises"] = state["surprises"][-30:]
        net["n"] += 1
        net["abs"] += s["abs_err"]
        groups.setdefault((s["slug"], s["h"], s["t"]), {})[s["net"]] = s["abs_err"]
    for (slug, h, t), errs in sorted(groups.items()):
        if all(n in errs for n in BASELINES):
            slot = agg_slot(state, slug, h)
            slot["pair"]["n"] += 1
            for n in BASELINES:
                slot["pair"]["abs"][n] += errs[n]
            slot["recent"] = (slot["recent"] + [[t, {n: round(errs[n], 4) for n in errs}]])[-RECENT:]
        if all(n in errs for n in NETS):
            slot = agg_slot(state, slug, h)
            slot["tri"]["n"] += 1
            for n in NETS:
                slot["tri"]["abs"][n] += errs[n]
        state["counts"]["scored"] += len(errs)


def rebuild_state(log: Path) -> dict:
    """Derive the state from the log alone (used when the state file is lost)."""
    state = fresh_state()
    forecasts, outcomes = {}, {}
    if log.is_file():
        with open(log, "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("k") == "forecast":
                    forecasts[rec["id"]] = rec
                elif rec.get("k") == "outcome":
                    outcomes[rec["id"]] = rec
    scored = []
    for fid, rec in sorted(forecasts.items(), key=lambda kv: (kv[1]["t"], kv[0])):
        key = f"{rec['net']}|{rec['slug']}|{rec['h']}"
        state["last"][key] = max(state["last"].get(key, 0), rec["t"])
        state["counts"]["forecasts"] += 1
        state["first_live"] = min(state["first_live"] or rec["made"], rec["made"])
        out = outcomes.get(fid)
        if out is None:
            state["pending"][fid] = {k: rec[k] for k in ("t", "net", "slug", "h", "v", "made")}
        elif out.get("state") == "scored":
            scored.append({"net": rec["net"], "slug": rec["slug"], "h": rec["h"], "t": rec["t"],
                           "abs_err": abs(out["err"]), "forecast": rec["v"], "obs": out["obs"]})
        else:
            state["counts"]["unobserved"] += 1
    apply_scored(state, scored)
    return state


def load_state(path: Path, log: Path) -> dict:
    try:
        doc = json.loads(path.read_text())
        if isinstance(doc, dict) and doc.get("v") == 1 and isinstance(doc.get("pending"), dict):
            return doc
    except (OSError, ValueError):
        pass
    return rebuild_state(log)


def table_row(slot: dict) -> dict:
    p = slot["pair"]
    row = {"n": p["n"], "mae": {n: (p["abs"][n] / p["n"] if p["n"] else None) for n in BASELINES}}
    row["verdict"] = verdict(row)
    t = slot["tri"]
    row["damp"] = net_row(t["n"], {n: (t["abs"][n] / t["n"] if t["n"] else None) for n in NETS})
    return row


def net_row(n: int, mae: dict) -> dict:
    """The damped net against the better baseline on the same hours. Counts only with MIN_PAIRS such hours."""
    row = {"n": n, "mae": mae, "edge": None}
    better = min(mae["persist"], mae["how"]) if n and mae["persist"] is not None and mae["how"] is not None else None
    if n < MIN_PAIRS or mae.get("damp") is None or better is None:
        row["verdict"] = f"too few ({n}/{MIN_PAIRS})"
    elif better == 0:
        row["verdict"] = "no difference"
    else:
        row["edge"] = round(1 - mae["damp"] / better, 3)
        if row["edge"] >= 0.05:
            row["verdict"] = "damped beats the better baseline"
        elif row["edge"] <= -0.05:
            row["verdict"] = "damped loses to the better baseline"
        else:
            row["verdict"] = "no difference"
    return row


def verdict(row: dict) -> str:
    n, m = row["n"], row["mae"]
    if n < MIN_PAIRS or m["persist"] is None or m["how"] is None:
        return f"too few ({n}/{MIN_PAIRS})"
    if m["persist"] == 0 and m["how"] == 0:
        return "no difference"
    edge = 1 - m["how"] / m["persist"] if m["persist"] > 0 else None
    if edge is None:
        return "last value wins"
    row["edge"] = round(edge, 3)
    if edge >= 0.05:
        return "same-hour beats last-value"
    if edge <= -0.05:
        return "last-value beats same-hour"
    return "no difference"


def replay(series: dict, hours: list, days: int = 14) -> dict:
    """Walk-forward replay over the retained store: what each baseline would have said, using only earlier
    hours. Retrospective, so it says nothing about live skill; it only gives the first scoreboard."""
    out = {}
    if not hours:
        return out
    lo = max(hours) - days * 24 * HOUR
    for slug, s in series.items():
        for h in HORIZONS:
            n, sums = 0, {k: 0.0 for k in BASELINES}
            tn, tsums = 0, {k: 0.0 for k in NETS}
            for t in sorted(s.values):
                if t < lo:
                    continue
                c = t - h * HOUR
                f = {net: s.forecast(net, c, h) for net in NETS}
                if all(f[net] is not None for net in BASELINES):
                    n += 1
                    for net in BASELINES:
                        sums[net] += abs(f[net][0] - s.values[t])
                if all(v is not None for v in f.values()):
                    tn += 1
                    for net in NETS:
                        tsums[net] += abs(f[net][0] - s.values[t])
            row = {"n": n, "mae": {net: (sums[net] / n if n else None) for net in BASELINES}}
            row["verdict"] = verdict(row)
            row["damp"] = net_row(tn, {net: (tsums[net] / tn if tn else None) for net in NETS})
            out[f"{slug}|{h}"] = row
    return out


def run(site: Path, now: float) -> tuple[int, dict]:
    fdir = site / "features"
    store = fdir / "house-hourly.jsonl"
    detail = {"forecasts": 0, "scored": 0, "unobserved": 0}
    rows = load_store(store)
    if not rows:
        detail["reason"] = f"no feature store rows at {store}"
        return 4, detail
    log, state_path, score_path = fdir / "predictions.jsonl", fdir / "predictions.state.json", fdir / "predictions-score.json"
    state = load_state(state_path, log)
    c = hour_start(now)
    series = {slug: Series({t: v for t, r in rows.items() if (v := value_of(r, slug)) is not None}) for slug in TARGETS}
    records, scored = [], []
    for fid, p in sorted(state["pending"].items(), key=lambda kv: (kv[1]["t"], kv[0])):
        row = rows.get(p["t"])
        obs = value_of(row, p["slug"]) if row else None
        if obs is not None:
            err = p["v"] - obs
            records.append({"k": "outcome", "id": fid, "state": "scored", "obs": obs, "err": err, "at": int(now),
                            "net": p["net"], "slug": p["slug"], "h": p["h"], "t": p["t"]})
            scored.append({"net": p["net"], "slug": p["slug"], "h": p["h"], "t": p["t"], "abs_err": abs(err),
                           "forecast": p["v"], "obs": obs})
        elif row is not None or now > p["t"] + UNOBSERVED_AFTER_S:
            records.append({"k": "outcome", "id": fid, "state": "unobserved", "at": int(now),
                            "reason": "hour has no value for this target" if row is not None else "no store row for the hour",
                            "net": p["net"], "slug": p["slug"], "h": p["h"], "t": p["t"]})
    stale = max(rows) < c - STALE_S
    forecasts = []
    if not stale:
        for h in HORIZONS:
            target_t = c + h * HOUR
            for slug in TARGETS:
                for net in NETS:
                    key = f"{net}|{slug}|{h}"
                    if state["last"].get(key, 0) >= target_t:
                        continue
                    got = series[slug].forecast(net, c, h)
                    if got is None:
                        continue
                    fid = f"{net}|{slug}|{h}|{target_t}"
                    forecasts.append({"k": "forecast", "id": fid, "mode": "live", "made": int(now), "t": target_t,
                                      "net": net, "slug": slug, "h": h, "v": got[0], "basis": got[1]})
    append_log(log, records + forecasts)               # one write: nothing is scored or forecast without a log line
    for r in records:
        state["pending"].pop(r["id"])
        if r["state"] == "unobserved":
            state["counts"]["unobserved"] += 1
    apply_scored(state, scored)
    for f in forecasts:
        state["pending"][f["id"]] = {k: f[k] for k in ("t", "net", "slug", "h", "v", "made")}
        state["last"][f"{f['net']}|{f['slug']}|{f['h']}"] = f["t"]
        state["counts"]["forecasts"] += 1
        state["first_live"] = state["first_live"] or f["made"]
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    os.replace(tmp, state_path)
    nxt = {}
    for fid, p in state["pending"].items():
        if p["t"] in (c + HOUR, c + 24 * HOUR):
            nxt.setdefault(p["slug"], {}).setdefault(str(p["h"]), {})[p["net"]] = p["v"]
    score = {"v": 1, "generated": int(now), "store": {"first": min(rows), "last": max(rows), "hours": len(rows)},
             "stale": stale, "counts": state["counts"], "first_live": state["first_live"],
             "chain": verify_chain(log), "targets": {k: v[0] for k, v in TARGETS.items()},
             "live": {k: table_row(v) for k, v in sorted(state["agg"].items())},
             "replay": replay(series, sorted(rows)), "surprises": state["surprises"][-10:],
             "next": nxt, "pending": len(state["pending"])}
    tmp = score_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(score, sort_keys=True))
    os.replace(tmp, score_path)
    detail.update(forecasts=len(forecasts), scored=len(scored),
                  unobserved=sum(1 for r in records if r["state"] == "unobserved"), pending=len(state["pending"]),
                  chain=score["chain"]["state"], last_hour=time.strftime("%Y-%m-%dT%H:00Z", time.gmtime(max(rows))))
    if stale:
        detail["reason"] = "newest store hour is over 4 h old: no forecast was made"
        return 4, detail
    return 0, detail


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--now", type=float, default=None, help="epoch seconds (tests)")
    args = parser.parse_args(argv)
    code, detail = run(args.site, args.now if args.now is not None else time.time())
    print(json.dumps(detail, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
