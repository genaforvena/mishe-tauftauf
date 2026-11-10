#!/usr/bin/env python3
"""Per-wake cost rows: what a yielded wake actually spent, per model.

The budget view aggregates tokens per account, but the routing decision Ilya
asked for (2026-11-10, DMs 299393/299396/299397) is per *wake*: roubles per
yielded wake, cached-token share, wake seconds and fall-throughs for
ling-3.1-flash vs the models it replaced. Wakes are the unit the router
spends, so this reads the two records the rest of the site already keeps:

- ``wakes/wake-<key>.yield.json`` — role, result (changed/verified/blocked),
  settled_at; a wake without a yield record has not settled.
- ``wakes/wake-<key>.prompt.out.jsonl`` — one ``message_end`` per assistant
  turn, decoded through the committed boundary ``omp_usage_text.decode_line``,
  which returns (seconds, ``provider|model``, response id, [turns, output,
  input, cacheRead, cacheWrite, provider-reported cost.total]).

Every provider/model pair on the stream gets one row, so a fall-through
between candidates keeps both rows: ling rows price roubles, the claude-engine
haiku row prices the subscription turn count. Only wake streams are read here;
the per-account token totals stay in the collector. The boundary decoder
raises :class:`Uncountable` for a turn whose cost total is not a number, so
such turns are skipped and counted: a wake with any skipped turn is
``measured=False`` — its sums cover only the turns the provider priced and it
must never be compared as a whole wake. The engine seconds come from the
stream's first and last decoded turn, not from wall clocks outside the wake.
Output is one JSON object per line with ``--json``; the default is a table.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from mishe_tauftauf.omp_usage_text import Uncountable, decode_line  # noqa: E402

YIELD_SUFFIX = ".yield.json"
STREAM_SUFFIX = ".prompt.out.jsonl"
ROUTERAI = "routerai"


def wake_rows(wake_dir: Path, since: float = 0.0):
    """Aggregate one wake stream per (wake, model); join the yield record.

    Returns rows in wake-completion order. A wake whose stream decodes to no
    usage turn at all is skipped (the collector's Unparsed lane covers
    undecodable files).
    """
    rows = []
    stream_paths = sorted(glob.glob(str(wake_dir / ("wake-*" + STREAM_SUFFIX))), key=os.path.getmtime)
    for stream in stream_paths:
        base = os.path.basename(stream)
        wake_key = base[: -len(STREAM_SUFFIX)].removeprefix("wake-")
        if not wake_key:
            continue
        yield_path = wake_dir / ("wake-" + wake_key + YIELD_SUFFIX)
        yield_rec = None
        if yield_path.exists():
            try:
                yield_rec = json.loads(yield_path.read_text())
            except (OSError, ValueError):
                yield_rec = None
        per_model: dict[str, dict] = {}
        first_s = last_s = None
        unpriced = 0
        for line in open(stream, encoding="utf-8"):
            try:
                decoded = decode_line(line)
            except Uncountable:
                unpriced += 1
                continue
            if decoded is None:
                continue
            ts_s, name, _response_id, values = decoded
            provider = name.split("|", 1)[0] if "|" in name else ""
            if provider == ROUTERAI and first_s is None:
                first_s = ts_s
            if provider == ROUTERAI:
                last_s = ts_s
            vals = per_model.setdefault(
                name,
                {"turns": 0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0},
            )
            vals["turns"] += values[0]
            vals["out"] += values[1]
            vals["in"] += values[2]
            vals["cache_read"] += values[3]
            vals["cache_write"] += values[4]
            vals["cost"] += values[5]
        if not per_model:
            continue
        seconds = None
        if first_s is not None and last_s is not None:
            seconds = last_s - first_s
        for name, vals in per_model.items():
            provider, model = name.split("|", 1)
            usage_total = vals["in"] + vals["out"] + vals["cache_read"] + vals["cache_write"]
            rows.append(
                {
                    "wake": wake_key,
                    "role": (yield_rec or {}).get("role") or wake_key.split("-", 1)[0],
                    "result": (yield_rec or {}).get("result"),
                    "settled_at": (yield_rec or {}).get("settled_at"),
                    "provider": provider,
                    "model": model,
                    "turns": vals["turns"],
                    "input": vals["in"],
                    "output": vals["out"],
                    "cache_read": vals["cache_read"],
                    "cache_write": vals["cache_write"],
                    "cost_currency": "RUB" if provider == ROUTERAI else ("USD-sub" if provider == "claude" else "USD"),
                    "cost": round(vals["cost"], 4),
                    "unpriced_turns": unpriced if len(per_model) == 1 else None,
                    "measured": (unpriced == 0 and vals["turns"] > 0) if len(per_model) == 1 else None,
                    "wake_seconds": round(seconds, 1) if seconds is not None else None,
                    "cache_share": (vals["cache_read"] / usage_total) if usage_total > 0 else None,
                    "stream_mtime": os.path.getmtime(stream),
                }
            )
    return [r for r in rows if r["stream_mtime"] >= since]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", default=str(REPO / ".mishe-tauftauf"), help="site home holding wakes/")
    ap.add_argument("--hours", type=float, default=24.0, help="only wakes whose stream completed within this window")
    ap.add_argument("--json", action="store_true", help="emit JSON lines instead of the table")
    args = ap.parse_args(argv)
    since = time.time() - args.hours * 3600
    rows = wake_rows(Path(args.site) / "wakes", since=since)
    if args.json:
        for r in rows:
            print(json.dumps(r))
        return 0
    print(f"{'time':^5} {'wake':<36} {'role':<10} {'result':<8} {'model':<34} {'msgs':>4} {'in':>7} {'out':>7} {'cacheR':>7} {'cost':>7} {'cache%':>6} {'secS':>6}")
    for r in rows:
        t = time.strftime("%H:%M", time.localtime(r["stream_mtime"]))
        cur = {"RUB": "₽", "USD": "$", "USD-sub": "sub"}[r["cost_currency"]]
        cost = f"{r['cost']:.2f}{cur}" if r["measured"] else f"{r['cost']:.2f}{cur}?"
        share = f"{100 * r['cache_share']:.0f}" if r["cache_share"] is not None else "-"
        secs = f"{r['wake_seconds']:.0f}" if r["wake_seconds"] is not None else "-"
        model = r["model"][:33]
        print(f"{t:^5} {r['wake']:<36} {r['role']:<10} {str(r['result']):<8} {model:<34} {r['turns']:>4} {r['input'] / 1e3:>7.1f}k {r['output'] / 1e3:>7.1f}k {r['cache_read'] / 1e6:>6.1f}M {cost:>7} {share:>6} {secs:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
