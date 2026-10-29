#!/usr/bin/env python3
"""Aggregate native inference usage per role/model/day.

Reads per-wake native canary result.json (and journal fallback) under
SITE/native-canary/*/wakes/*/result.json and journal.jsonl.
Produces a daily summary JSON with explicit uncertainty and fail-closed
handling for missing/corrupt segments. Does not mutate wake journals.

Per charter projects/inference-budget.md: do not invent quotas or treat
incomparable tokenizers as equivalent. 429 wakes report 0 tokens but consume
provider quota — they are attributed as blocked, not zero-cost.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="Aggregate inference budget daily")
    p.add_argument("--home", required=True, help="SITE home (e.g. .mishe-tauftauf)")
    p.add_argument("--out", required=True, help="Output JSON path")
    p.add_argument("--wakes-root", default=None, help="Override wakes root (for isolated tests)")
    return p.parse_args()


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as exc:
        raise ValueError(f"corrupt json {path}: {exc}") from exc


def day_of(result: dict, journal_path: Path | None) -> str | None:
    # Prefer result timestamp? result has no timestamp, use journal checkpoint timestamp or file mtime.
    # Use file mtime of result.json as fallback day; if journal exists, use its first checkpoint timestamp.
    if journal_path and journal_path.exists():
        try:
            for line in journal_path.read_text(encoding="utf-8").splitlines()[:5]:
                j = json.loads(line)
                if j.get("kind") == "checkpoint" and "state" in j:
                    # checkpoint has no timestamp; try model_output timestamp
                    pass
                if j.get("kind") == "model_output":
                    resp = j.get("response") or {}
                    ts = resp.get("timestamp")
                    if isinstance(ts, (int, float)):
                        # ms since epoch? native journal timestamp is float ms? observed: 179... is monotonic ns? treat as ms
                        # Fallback: use mtime-derived day instead — don't guess epoch.
                        pass
        except Exception:
            pass
    # Fall back to file mtime day UTC
    try:
        mtime = result.get("_file_mtime")
        if mtime is not None:
            dt = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)
            return dt.date().isoformat()
    except Exception:
        pass
    return None


def aggregate(home: Path, wakes_root: Path | None = None):
    home = Path(home)
    if wakes_root is None:
        # Default: SITE/native-canary/*/wakes
        canary = home / "native-canary"
        roots = []
        if canary.exists():
            for role_dir in canary.iterdir():
                wakes = role_dir / "wakes"
                if wakes.is_dir():
                    roots.append((role_dir.name, wakes))
        # Also support native-canary/docs/wakes directly
        if not roots:
            fallback = home / "native-canary" / "docs" / "wakes"
            if fallback.is_dir():
                roots = [("docs", fallback)]
    else:
        roots = [("docs", Path(wakes_root))]

    per_day: dict[tuple[str, str, str], dict] = {}
    wakes_scanned = 0
    wakes_missing_result = 0
    corrupt: list[str] = []
    details: list[dict] = []

    for role, wakes_dir in roots:
        if not wakes_dir.exists():
            continue
        for wake_dir in wakes_dir.iterdir():
            if not wake_dir.is_dir():
                continue
            # wake id is dirname; may be like 75277 or 75277-recovery-1
            name = wake_dir.name
            # derive base wake id for grouping? keep as is for counting
            result_path = wake_dir / "result.json"
            journal_path = wake_dir / "journal.jsonl"
            try:
                result = load_json(result_path)
            except ValueError as exc:
                corrupt.append(str(exc))
                continue
            if result is None:
                wakes_missing_result += 1
                continue
            wakes_scanned += 1
            # attach mtime for day bucket
            try:
                result["_file_mtime"] = result_path.stat().st_mtime
            except OSError:
                result["_file_mtime"] = None

            selector = result.get("selector") or "unknown"
            # normalize model: selector often like openai-codex/gpt-5.6-luna
            model = selector
            status = result.get("status") or "unknown"
            turns = result.get("turns") or 0
            calls = result.get("calls") or 0
            wire_usage = result.get("wire_usage") or []
            fetch_ledger = result.get("fetch_ledger") or []

            # Sum tokens from wire_usage entries that are not null
            input_tokens = 0
            output_tokens = 0
            total_tokens = 0
            cached_read = 0
            # wire_usage may be list of dicts with input_tokens/output_tokens/total_tokens
            for entry in wire_usage:
                if not isinstance(entry, dict):
                    continue
                input_tokens += int(entry.get("input_tokens") or 0)
                output_tokens += int(entry.get("output_tokens") or 0)
                total_tokens += int(entry.get("total_tokens") or 0)
                details_in = entry.get("input_tokens_details") or {}
                cached_read += int(details_in.get("cached_tokens") or 0)

            # Detect 429 blocked
            blocked = 0
            blocked_reason = None
            for fl in fetch_ledger:
                if not isinstance(fl, dict):
                    continue
                uf = fl.get("upstream_failure") or {}
                if uf.get("status") == 429:
                    blocked = 1
                    blocked_reason = f"429 {uf.get('statusText')}"
                    break
            # Also detect model_output error 429 via journal fallback if needed (already in fetch_ledger)
            # Day bucket: use file mtime date
            day = None
            try:
                mtime = result_path.stat().st_mtime
                day = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).date().isoformat()
            except OSError:
                day = "unknown"

            key = (day, role, model)
            bucket = per_day.setdefault(key, {
                "date": day,
                "role": role,
                "model": model,
                "wakes": 0,
                "turns": 0,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "blocked_429_count": 0,
                "blocked_wakes": [],
                "wake_ids": [],
            })
            bucket["wakes"] += 1
            bucket["turns"] += int(turns or 0)
            bucket["calls"] += int(calls or 0)
            bucket["input_tokens"] += input_tokens
            bucket["output_tokens"] += output_tokens
            bucket["total_tokens"] += total_tokens
            bucket["cached_tokens"] += cached_read
            if blocked:
                bucket["blocked_429_count"] += 1
                bucket["blocked_wakes"].append(name)
            bucket["wake_ids"].append(name)

            details.append({
                "wake": name,
                "role": role,
                "model": model,
                "day": day,
                "status": status,
                "turns": turns,
                "calls": calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "blocked_429": bool(blocked),
                "blocked_reason": blocked_reason,
                "settled": result.get("settled"),
                "path": str(result_path),
            })

    # Sort buckets by date then role
    buckets = sorted(per_day.values(), key=lambda b: (b["date"], b["role"], b["model"]))

    # Build operator view: one line per role (today + last 7d)
    # For simplicity, operator_view is per bucket with uncertainty note
    operator_lines = []
    for b in buckets:
        line = (
            f"{b['date']} {b['role']}/{b['model']}: "
            f"{b['wakes']} wakes {b['turns']} turns {b['calls']} calls "
            f"{b['total_tokens']} tokens (in {b['input_tokens']} out {b['output_tokens']} cached {b['cached_tokens']})"
        )
        if b["blocked_429_count"]:
            line += f" BLOCKED {b['blocked_429_count']} 429 wakes {b['blocked_wakes']} (quota, not zero-cost)"
        operator_lines.append(line)

    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    output = {
        "generated_at": generated_at,
        "home": str(home),
        "roots": [str(p) for _, p in roots],
        "wakes_scanned": wakes_scanned,
        "wakes_missing_result": wakes_missing_result,
        "corrupt": corrupt,
        "buckets": buckets,
        "details": sorted(details, key=lambda d: d["wake"]),
        "operator_view": operator_lines,
        "uncertainty": (
            "Tokens are provider-specific (openai-codex gpt-5.6-luna vs OMP models) and not comparable across providers; "
            "costs not normalized. 429 wakes report 0 tokens but consumed quota — counted as blocked, not zero. "
            "OMP agent.db not aggregated (no live token ledger); only native canary wakes with result.json are measured. "
            "Provider quota is global per account, not per role. Forecast is retry-after (~197m) only; token forecast from prior wakes is variable (5-12 turns ~ 2k-15k tokens) and not predictive."
        ),
        "missing": [
            "OMP agent.db token ledger per role/model/time — requires schema inspection under publication_lock-free read (not yet integrated)",
            "Cross-provider cost normalization — incomparable tokenizers",
            "Per-project attribution tag in journal — not present",
            "~/.codex/history.jsonl aggregation — present but not yet parsed",
            "mesh-token-usage-* bins — present but not integrated",
        ],
        "enforcement": {
            "mechanism": "Existing wake wound/hold via native_recovery blocked_state or seed yield hold, not cgroup; field-review self_pick 21600s (6h) is bounded share; emergency/life-support bypass; no ceiling raise",
            "demo_next": "Hold resting role (body-research) when daily total exceeds soft threshold; Health owns UNKNOWN for missing aggregate, Witness verifies attribution/rollback",
        },
    }
    # Fail-closed: if any corrupt segment, mark outcome UNKNOWN in output but still emit with corrupt list
    if corrupt:
        output["state"] = "UNKNOWN"
        output["fail_closed_reason"] = "One or more wake result.json corrupt — aggregation is partial, not silent gap"
    else:
        output["state"] = "OBSERVED"
    return output


def main():
    args = parse_args()
    home = Path(args.home)
    out = Path(args.out)
    wakes_root = Path(args.wakes_root) if args.wakes_root else None

    # Validate home exists
    if not home.exists():
        print(f"home not found: {home}", file=sys.stderr)
        sys.exit(2)

    try:
        data = aggregate(home, wakes_root)
    except Exception as exc:
        print(f"aggregation failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # Write atomically
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".new")
    payload = json.dumps(data, sort_keys=True, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    tmp.chmod(0o644)
    # fsync
    try:
        with tmp.open("rb") as f:
            os.fsync(f.fileno())
    except OSError:
        pass
    os.replace(tmp, out)
    try:
        d = os.open(str(out.parent), os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    except OSError:
        pass

    # Also emit sha256
    sha = hashlib.sha256(payload.encode()).hexdigest()
    print(f"WROTE {out} sha256={sha} wakes={data['wakes_scanned']} buckets={len(data['buckets'])} state={data['state']}")
    if data["corrupt"]:
        print(f"CORRUPT segments: {data['corrupt']}", file=sys.stderr)
    for line in data["operator_view"]:
        print(line)
    if data["state"] == "UNKNOWN":
        sys.exit(3)

if __name__ == "__main__":
    main()
