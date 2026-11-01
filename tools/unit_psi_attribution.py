"""Per-unit attribution over the operator cgroup sampler JSONL (read-only).

Answers, for the newest sampler record: which units carry the most full-pressure
(psi.full.avg60), how their memory.events high/max counters moved over the window,
and how their swap sits against swap.max. It reuses the sampler parser from
unit_psi_reading and writes nothing.

Unknown is never zero: a counter delta with no anchor record in the window is
reported as None, and an uncapped swap.max is reported as "uncapped", not 0%.

Usage: python tools/unit_psi_attribution.py [PATH] [--window-s N] [--top N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mishe_tauftauf.unit_psi_text import parse_jsonl_records  # noqa: E402

DEFAULT_PATH = ROOT / ".mishe-tauftauf" / "operator" / "unit-psi.jsonl"
DEFAULT_WINDOW_S = 300
DEFAULT_TOP = 3


def _num(mapping, *keys):
    cur = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return float(cur) if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None


def _counter(cgroup: dict, name: str):
    value = _num(cgroup, "events", name)
    return int(value) if value is not None else None


def _swap(cgroup: dict):
    swap = cgroup.get("swap")
    swap_max = cgroup.get("swap_max")
    if not isinstance(swap, (int, float)) or isinstance(swap, bool):
        return None, "unknown"
    if isinstance(swap_max, (int, float)) and not isinstance(swap_max, bool) and swap_max > 0:
        return float(swap), f"{swap / swap_max:.1%}"
    return float(swap), "uncapped"


def attribute(records: list[dict], *, window_s: float = DEFAULT_WINDOW_S,
              top: int = DEFAULT_TOP) -> dict:
    """Attribution dict for the newest record in `records` (pure; no I/O)."""
    if not records:
        return {"state": "unknown", "reason": "no records"}
    ordered = sorted(records, key=lambda r: float(r["at"]))
    latest = ordered[-1]
    at = float(latest["at"])
    units = latest.get("cgroups") or {}
    if not units:
        return {"state": "unknown", "reason": "newest record has no cgroup entries"}

    anchor = next((r for r in ordered[:-1] if at - float(r["at"]) <= window_s), None)
    span = (at - float(anchor["at"])) if anchor else None

    rows = []
    for name, cgroup in units.items():
        if not isinstance(cgroup, dict):
            continue
        then = anchor["cgroups"].get(name) if anchor else None
        then = then if isinstance(then, dict) else None

        def delta(counter):
            now_v = _counter(cgroup, counter)
            then_v = _counter(then, counter) if then is not None else None
            return None if now_v is None or then_v is None or anchor is None else now_v - then_v

        swap_bytes, swap_frac = _swap(cgroup)
        rows.append({
            "unit": name,
            "full_avg60": _num(cgroup, "psi", "full", "avg60"),
            "some_avg60": _num(cgroup, "psi", "some", "avg60"),
            "high_delta": delta("high"),
            "max_delta": delta("max"),
            "swap_bytes": swap_bytes,
            "swap_of_max": swap_frac,
        })

    ranked = sorted(
        (r for r in rows if r["full_avg60"] is not None),
        key=lambda r: r["full_avg60"], reverse=True,
    )
    return {
        "state": "verified" if ranked else "unknown",
        "at": at,
        "span_s": span,
        "units_total": len(rows),
        "units_with_full_avg60": len(ranked),
        "top_full_avg60": ranked[:top],
        "high_or_max_moved": sorted(
            (r for r in rows if (r["high_delta"] or 0) > 0 or (r["max_delta"] or 0) > 0),
            key=lambda r: (r["high_delta"] or 0) + (r["max_delta"] or 0), reverse=True,
        ),
        "swap_units": sorted(
            (r for r in rows if r["swap_bytes"]), key=lambda r: r["swap_bytes"], reverse=True,
        )[:top],
    }


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(DEFAULT_PATH))
    parser.add_argument("--window-s", type=float, default=DEFAULT_WINDOW_S)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    args = parser.parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(json.dumps({"state": "unknown", "reason": f"absent: {path}"}))
        return 0
    records, malformed = parse_jsonl_records(path.read_bytes())
    out = attribute(records, window_s=args.window_s, top=args.top)
    out["malformed"] = malformed
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
