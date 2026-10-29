"""Pure aggregation + sense for OMP stats tokens, mirroring engine_usage.

Aggregates stats_boundary.query results over a time window and reports
FRESH/STALE/UNKNOWN with breakdowns by provider/model/folder (plant).
Cost UNKNOWN: cost_total==0 and unpriced_requests>0 => cost is N/A.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import stats_boundary

WINDOWS = {
    "1h": 3600000,
    "24h": 86400000,
    "7d": 604800000,
    "30d": 2592000000,
    "90d": 7776000000,
    "all": None,
}

TTL_MS = 60_000


def _iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_rate(cache_read, inp):
    denom = (inp or 0) + (cache_read or 0)
    if denom > 0:
        return (cache_read or 0) / denom
    return 0.0


def _summarize_rows(rows):
    """Sum decoded grouped rows into totals."""
    total_input = sum(r.get("sum_input") or 0 for r in rows)
    total_output = sum(r.get("sum_output") or 0 for r in rows)
    total_cache_read = sum(r.get("sum_cache_read") or 0 for r in rows)
    total_cache_write = sum(r.get("sum_cache_write") or 0 for r in rows)
    total_tokens = sum(r.get("sum_total") or 0 for r in rows)
    total_cost = sum(r.get("sum_cost") or 0 for r in rows)
    total_premium = sum(r.get("sum_premium") or 0 for r in rows)
    total_requests = sum(r.get("requests") or 0 for r in rows)
    unpriced = sum(r.get("unpriced_requests") or 0 for r in rows)
    return {
        "input": int(total_input),
        "output": int(total_output),
        "cache_read": int(total_cache_read),
        "cache_write": int(total_cache_write),
        "total": int(total_tokens),
        "tokens": int(total_tokens),
        "cost_total": float(total_cost),
        "cost": float(total_cost),
        "unpriced_requests": int(unpriced),
        "premium_requests": float(total_premium),
        "requests": int(total_requests),
        "cacheRate": _cache_rate(total_cache_read, total_input),
    }


def report(home, window="24h", provider=None, model=None, folder=None, now_ms=None, db_path=None):
    """Aggregate token usage over window.

    home: site home (unused for DB path which is global via get_stats_db_path,
          but accepted for API parity with engine_usage).
    window: key in WINDOWS ('1h','24h','7d','30d','90d','all').
    provider/model/folder: optional filters. folder suffix starting with '-' is
          applied as LIKE '%suffix' (whole plant). folder=='all' means no filter
          but still emits by_folder grouping (alias by_plant).
    Returns dict with state, observed_at, window, since_ms, db_mtime,
          age_seconds, totals, by_provider, by_model, by_folder, by_plant.
    """
    now = int(now_ms) if now_ms is not None else int(time.time() * 1000)
    win = window if window in WINDOWS else "24h"
    span = WINDOWS[win]
    since_ms = (now - span) if span is not None else None

    # Normalize folder filter: 'all' alias => no filtering
    folder_filter = None
    if folder is not None and folder != "all":
        folder_filter = folder
    # provider/model normalized: None stays None
    prov_filter = provider
    model_filter = model

    # Resolve db path
    if db_path is None:
        try:
            resolved = stats_boundary.get_stats_db_path()
        except Exception:
            resolved = None
    else:
        resolved = Path(db_path)

    # Single grouped query by provider,model,folder then aggregate in memory
    try:
        result = stats_boundary.query(
            db_path=resolved,
            since_ms=since_ms,
            until_ms=None,
            group_by=["provider", "model", "folder"],
            provider=prov_filter,
            model=model_filter,
            folder=folder_filter,
        )
    except Exception:
        result = {"rows": [], "error": "unreadable", "db_mtime": None, "unpriced_requests": 0}

    error = result.get("error")
    rows = result.get("rows") or []
    db_mtime = result.get("db_mtime")

    observed_at = _iso(now)
    age_seconds = None
    if db_mtime is not None:
        age_seconds = (now - db_mtime) / 1000.0

    if error is not None:
        state = "UNKNOWN"
        totals = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
            "total": 0,
            "tokens": 0,
            "cost_total": 0.0,
            "cost": 0.0,
            "unpriced_requests": 0,
            "premium_requests": 0.0,
            "requests": 0,
            "cacheRate": 0.0,
        }
        return {
            "state": state,
            "observed_at": observed_at,
            "window": win,
            "since_ms": since_ms,
            "until_ms": None,
            "db_mtime": db_mtime,
            "age_seconds": age_seconds,
            "error": error,
            "totals": totals,
            "by_provider": {},
            "by_model": [],
            "by_folder": [],
            "by_plant": [],
        }

    # Derive aggregates
    totals = _summarize_rows(rows)

    # by_provider
    by_provider = {}
    for r in rows:
        p = r.get("provider")
        if p not in by_provider:
            by_provider[p] = {
                "provider": p,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total": 0,
                "tokens": 0,
                "cost_total": 0.0,
                "cost": 0.0,
                "unpriced_requests": 0,
                "premium_requests": 0.0,
                "requests": 0,
            }
        agg = by_provider[p]
        agg["input"] += int(r.get("sum_input") or 0)
        agg["output"] += int(r.get("sum_output") or 0)
        agg["cache_read"] += int(r.get("sum_cache_read") or 0)
        agg["cache_write"] += int(r.get("sum_cache_write") or 0)
        agg["total"] += int(r.get("sum_total") or 0)
        agg["tokens"] += int(r.get("sum_total") or 0)
        agg["cost_total"] += float(r.get("sum_cost") or 0)
        agg["cost"] += float(r.get("sum_cost") or 0)
        agg["unpriced_requests"] += int(r.get("unpriced_requests") or 0)
        agg["premium_requests"] += float(r.get("sum_premium") or 0)
        agg["requests"] += int(r.get("requests") or 0)
    for p, agg in by_provider.items():
        agg["cacheRate"] = _cache_rate(agg["cache_read"], agg["input"])

    # by_model
    # group by (provider, model)
    model_map = {}
    for r in rows:
        key = (r.get("provider"), r.get("model"))
        if key not in model_map:
            model_map[key] = {
                "provider": key[0],
                "model": key[1],
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total": 0,
                "tokens": 0,
                "cost_total": 0.0,
                "cost": 0.0,
                "unpriced_requests": 0,
                "premium_requests": 0.0,
                "requests": 0,
            }
        agg = model_map[key]
        agg["input"] += int(r.get("sum_input") or 0)
        agg["output"] += int(r.get("sum_output") or 0)
        agg["cache_read"] += int(r.get("sum_cache_read") or 0)
        agg["cache_write"] += int(r.get("sum_cache_write") or 0)
        agg["total"] += int(r.get("sum_total") or 0)
        agg["tokens"] += int(r.get("sum_total") or 0)
        agg["cost_total"] += float(r.get("sum_cost") or 0)
        agg["cost"] += float(r.get("sum_cost") or 0)
        agg["unpriced_requests"] += int(r.get("unpriced_requests") or 0)
        agg["premium_requests"] += float(r.get("sum_premium") or 0)
        agg["requests"] += int(r.get("requests") or 0)
    for v in model_map.values():
        v["cacheRate"] = _cache_rate(v["cache_read"], v["input"])
    by_model = sorted(model_map.values(), key=lambda x: (-x["tokens"], x["provider"], x["model"]))

    # by_folder / by_plant
    folder_map = {}
    for r in rows:
        f = r.get("folder")
        if f not in folder_map:
            folder_map[f] = {
                "folder": f,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total": 0,
                "tokens": 0,
                "cost_total": 0.0,
                "cost": 0.0,
                "unpriced_requests": 0,
                "premium_requests": 0.0,
                "requests": 0,
            }
        agg = folder_map[f]
        agg["input"] += int(r.get("sum_input") or 0)
        agg["output"] += int(r.get("sum_output") or 0)
        agg["cache_read"] += int(r.get("sum_cache_read") or 0)
        agg["cache_write"] += int(r.get("sum_cache_write") or 0)
        agg["total"] += int(r.get("sum_total") or 0)
        agg["tokens"] += int(r.get("sum_total") or 0)
        agg["cost_total"] += float(r.get("sum_cost") or 0)
        agg["cost"] += float(r.get("sum_cost") or 0)
        agg["unpriced_requests"] += int(r.get("unpriced_requests") or 0)
        agg["premium_requests"] += float(r.get("sum_premium") or 0)
        agg["requests"] += int(r.get("requests") or 0)
    for v in folder_map.values():
        v["cacheRate"] = _cache_rate(v["cache_read"], v["input"])
    by_folder = sorted(folder_map.values(), key=lambda x: (-x["tokens"], x["folder"]))
    by_plant = list(by_folder)  # alias

    # Determine state FRESH/STALE/UNKNOWN
    if db_mtime is None:
        state = "UNKNOWN"
    else:
        age_ms = now - db_mtime
        if age_ms < 0:
            # clock skew: treat as fresh if within 5s future
            state = "FRESH" if age_ms > -5000 else "UNKNOWN"
        elif age_ms <= TTL_MS:
            state = "FRESH"
        else:
            state = "STALE"

    return {
        "state": state,
        "observed_at": observed_at,
        "window": win,
        "since_ms": since_ms,
        "until_ms": None,
        "db_mtime": db_mtime,
        "age_seconds": age_seconds,
        "error": None,
        "totals": totals,
        "by_provider": by_provider,
        "by_model": by_model,
        "by_folder": by_folder,
        "by_plant": by_plant,
    }


def _format_cost(cost, unpriced_requests):
    if cost == 0 and unpriced_requests > 0:
        return "UNKNOWN"
    return f"${cost:.4f}"


def render(report):
    """Text table for non-json mode."""
    lines = []
    totals = report.get("totals", {})
    state = report.get("state")
    window = report.get("window")
    observed = report.get("observed_at")
    lines.append(f"stats {state}  window {window}  observed {observed}")
    if report.get("error"):
        lines.append(f"error: {report['error']}")
    cost_str = _format_cost(totals.get("cost_total", 0), totals.get("unpriced_requests", 0))
    lines.append(f"totals tokens={totals.get('total',0)} cost={cost_str} unpriced={totals.get('unpriced_requests',0)} cacheRate={totals.get('cacheRate',0):.2%}")
    # header
    lines.append(f"{'provider':<18} {'model':<24} {'folder':<24} {'tokens':>8} {'cost':>10}")
    for row in report.get("by_model", []):
        # limit to top? Show all
        prov = row.get("provider","")
        mod = row.get("model","")
        # For model rows folder is not part; show folder aggregated separately after?
        tokens = row.get("tokens",0)
        cost = _format_cost(row.get("cost_total",0), row.get("unpriced_requests",0))
        lines.append(f"{prov:<18} {mod:<24} {'':<24} {tokens:>8} {cost:>10}")
    for row in report.get("by_folder", []):
        folder = row.get("folder","")
        tokens = row.get("tokens",0)
        cost = _format_cost(row.get("cost_total",0), row.get("unpriced_requests",0))
        lines.append(f"{'':<18} {'':<24} {folder:<24} {tokens:>8} {cost:>10}")
    return "\n".join(lines)


def cmd_stats(args) -> int:
    rep = report(args.home, window=getattr(args, "window", "24h"), provider=getattr(args, "provider", None), model=getattr(args, "model", None), folder=getattr(args, "folder", None))
    if getattr(args, "json", False):
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        print(render(rep))
    return 0 if rep.get("state") == "FRESH" else 3


def register(sub):
    p = sub.add_parser("stats", help="Token usage from OMP stats.db (read-only, by provider/model/folder)")
    p.add_argument("--window", choices=list(WINDOWS.keys()), default="24h", help="time window")
    p.add_argument("--provider", default=None, help="filter by provider")
    p.add_argument("--model", default=None, help="filter by model")
    p.add_argument("--folder", default=None, help="filter by folder (suffix like -mishe-tauftauf or 'all')")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.set_defaults(func=cmd_stats)
