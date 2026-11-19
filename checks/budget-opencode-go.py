#!/usr/bin/env python3
"""Detector: the opencode-go allowance windows (go-a, glm-5.3 first choice in every role)
are measured, safe, and counted beside the ledger that selects them.

engine/budget.json is the one budget source (docs/budget-json-contract.md, produced by
``budget-produce`` every budget tick); its go-a account carries the provider's own
windows (5h, 7d weekly, monthly) with remaining/used, burn_h and eta_s. This check
judges only FRESH windows and mirrors the two existing quota rules written from the raw
fields (as budget-consistency does, not via the shared helpers): LOW on a spent window
(used > 0.9) and RUNS OUT BEFORE RESET when the burn ETA lands inside the window. The
weekly (7d) window is the allowance Ilya named, so an unmeasured 7d is UNKNOWN, never
admitted. Selection counts come from the routing ledger tail (go-a candidates only);
tokens per wake stay unattributed (state buckets are keyed provider|model, TOKEN_NOTE
in budget_producer) and are reported as such, not guessed.

Verdict is a state word for the producer machinery: exit 0 GREEN, 2 RED, 3 UNKNOWN.
AMBER is a grade inside the detail line (7d used at or past the operator's count-above
line of 50%), not a signal state, because producers observe only GREEN/RED/UNKNOWN.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

GREEN, RED, UNKNOWN = 0, 2, 3
ACCOUNT = "go-a"
PROVIDER_PREFIX = "omp:opencode-go/"
LEDGER_TAIL_BYTES = 1024 * 1024
USED_LOW = 0.9            # spent window (provider_budget's LOW rule)
BURN_MIN_USED = 0.05      # a forecast from a near-empty window is not actionable overburn
AMBER_USED = 0.5          # the operator's free-up-to-50% weekly line (standing approvals)
USD_FILE = "engine/go-a-weekly-usd"   # site-local allowance in USD; fractions only when absent


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _window_verdict(w: dict, now: float) -> tuple[str, str] | None:
    """(verdict, cause) for one FRESH window, or None when the window is malformed."""
    used, reset, eta = w.get("used"), w.get("reset_at"), w.get("eta_s")
    if not _num(used) or not _num(reset):
        return None
    if used > USED_LOW:
        return "low", f"{ACCOUNT} window used {used * 100:.0f}% (LOW)"
    if _num(eta) and used >= BURN_MIN_USED and now + eta < reset:
        return "runs-out", (f"{ACCOUNT} window burns out in {eta / 3600:.1f} h "
                            f"before reset in {(reset - now) / 3600:.1f} h (RUNS OUT BEFORE RESET)")
    return "ok", ""


def _fmt_window(w: dict) -> str:
    used = w.get("used")
    if not _num(used):
        return "unmeasured"
    parts = [f"used {used * 100:.0f}%"]
    if _num(w.get("burn_h")):
        parts.append(f"burn {w['burn_h'] * 100:.2f}%/h")
    if _num(w.get("eta_s")):
        parts.append(f"eta {w['eta_s'] / 3600:.0f}h")
    if _num(w.get("reset_at")):
        parts.append(f"reset {datetime.fromtimestamp(w['reset_at'], timezone.utc):%m-%d %H:%MZ}")
    return ", ".join(parts)


def _ledger_selections(path: Path, now: float) -> dict:
    """go-a selection counts from the ledger tail. The tail is a bounded read; when its
    earliest complete row is younger than the window, the count is a lower bound and the
    coverage instant is reported so the reader can see it."""
    out = {"1h": 0, "24h": 0, "models": {}, "coverage_s": None, "complete": True}
    if not path.exists():
        out["complete"] = False
        return out
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - LEDGER_TAIL_BYTES))
        blob = handle.read()
    lines = blob.split(b"\n")
    lines = lines[1:] if size > LEDGER_TAIL_BYTES else lines  # first fragment may be cut
    oldest = None
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if row.get("event") != "select":
            continue
        key = row.get("key")
        if not isinstance(key, str) or not key.startswith(PROVIDER_PREFIX):
            continue
        try:
            at = datetime.strptime(row["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        age = now - at.timestamp()
        if age < 0:
            age = 0.0
        oldest = age if oldest is None else max(oldest, age)
        out["24h"] += 1 if age <= 86400 else 0
        out["1h"] += 1 if age <= 3600 else 0
        model = key.split("/", 1)[1].split(":")[0] if "/" in key else key
        out["models"][model] = out["models"].get(model, 0) + 1
    if oldest is not None and oldest < 86400:
        out["complete"] = False
        out["coverage_s"] = int(oldest)
    return out


def _grade(windows: dict) -> str:
    used = (windows.get("7d") or {}).get("used")
    if _num(used):
        return "AMBER" if used >= AMBER_USED else "GREEN"
    return "UNKNOWN"


def evaluate(budget: dict, ledger: dict, now: float, weekly_usd=None) -> tuple[int, str]:
    """Pure verdict over the decoded budget payload; the ledger never changes the state,
    it only rides along so the subscriber reads the demand beside the window."""
    accounts = budget.get("accounts")
    if not isinstance(accounts, dict) or ACCOUNT not in accounts:
        return UNKNOWN, f"{ACCOUNT} account absent from engine budget (unmeasured)"
    account = accounts[ACCOUNT]
    if not isinstance(account, dict):
        return UNKNOWN, f"{ACCOUNT} account row malformed (unmeasured)"
    if account.get("state") != "FRESH":
        return UNKNOWN, f"{ACCOUNT} account state {account.get('state')!r}, not FRESH (unmeasured)"
    windows = account.get("windows")
    if not isinstance(windows, dict) or not windows:
        return UNKNOWN, f"{ACCOUNT} carries no windows (unmeasured)"
    weekly = windows.get("7d")
    if not isinstance(weekly, dict) or weekly.get("state") != "FRESH":
        return UNKNOWN, f"{ACCOUNT} 7d weekly window unmeasured (state {weekly.get('state') if isinstance(weekly, dict) else 'absent'!r})"
    parts = [f"{ACCOUNT} 7d {_fmt_window(weekly)}"]
    for name in ("5h", "monthly"):
        w = windows.get(name)
        if isinstance(w, dict) and w.get("state") == "FRESH":
            parts.append(f"{name} {_fmt_window(w)}")
    used = weekly.get("used")
    if weekly_usd and _num(used):
        parts.append(f"≈ ${weekly_usd * used:.0f} of ${weekly_usd:.0f} weekly allowance used")
    sel = ledger["24h"]
    note = f"selections 24h={sel}" + ("" if ledger["complete"] else "+ (lower bound, partial tail)")
    note += f" 1h={ledger['1h']}"
    if ledger["models"]:
        top = max(ledger["models"].items(), key=lambda kv: kv[1])
        note += f" top={top[0]} x{top[1]}"
    note += "; tokens-per-wake unattributed (state buckets keyed provider|model)"
    cause = ""
    # The judged set is the policy's own: routing admission and provider_budget both gate on
    # 5h/7d only. The monthly window rides along as context because its early-window burn
    # extrapolation is the same unactionable-overburn class the 2026-11-18 budget-claude
    # false RED came from (used 0.01 gave eta ~52 h against a ~161 h reset).
    for name in ("5h", "7d"):
        w = windows.get(name)
        if not isinstance(w, dict) or w.get("state") != "FRESH":
            continue
        verdict, why = _window_verdict(w, now)
        if verdict is None:
            return UNKNOWN, f"{ACCOUNT} {name} window malformed (unmeasured)"
        if verdict != "ok":
            return RED, why
    return GREEN, "; ".join(parts) + f"; {note}; grade {_grade(windows)}"


def _weekly_usd(home: Path):
    path = home / USD_FILE
    try:
        return float(path.read_text().strip())
    except (OSError, ValueError):
        return None


def run(home: Path, now: float, path: Path | None = None) -> tuple[int, dict]:
    budget_path = path or home / "engine" / "budget.json"
    ledger = _ledger_selections(home / "engine" / "routing-ledger.jsonl", now)
    try:
        budget = json.loads(budget_path.read_text())
    except (OSError, ValueError) as exc:
        return UNKNOWN, {"verdict": "UNKNOWN", "cause": f"engine budget unreadable: {exc}"}
    code, line = evaluate(budget, ledger, now, weekly_usd=_weekly_usd(home))
    state = {GREEN: "GREEN", RED: "RED", UNKNOWN: "UNKNOWN"}[code]
    return code, {"verdict": state, "account": ACCOUNT, "cause": line if code != GREEN else "",
                  "detail": line, "selections": ledger}


def main(argv: list[str] | None = None) -> int:
    here = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default=here, required=here is None, help="site home")
    parser.add_argument("--budget", default=None, help="budget.json path (default HOME/engine/budget.json)")
    parser.add_argument("--now", type=float, default=None, help="test override for the epoch clock")
    args = parser.parse_args(argv)
    home = Path(args.home)
    now = args.now if args.now is not None else datetime.now(timezone.utc).timestamp()
    code, report = run(home, now, Path(args.budget) if args.budget else None)
    print(f"budget-opencode-go: {report['verdict']}"
          + (f": {report['cause']}" if report.get("cause") else ""))
    print(report["detail"])
    return code


if __name__ == "__main__":
    sys.exit(main())
