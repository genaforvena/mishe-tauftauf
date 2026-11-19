#!/usr/bin/env python3
"""Detector: the signal of signals — every registered signal has a live producer, a
subscriber and a response (Ilya DM #303234: a signal without a listener is silent).

Registry is generated, not curated: ``signals/registry.json`` (what the producer
machinery itself registered: max_age, subscribers, registered_at per signal) plus the
duties manifest for accounts coverage. One worst case per signal, judged only from
bytes on disk:

- unmeasured: registered, never observed (no reading and no .seen) past the store's
  own grace deadline ``registered_at + max_age`` — a missing or dead producer.
- stale: the freshness stamp (.seen) is older than max_age — a producer that missed
  its cadence (the store's expire sweep ages the reading to UNKNOWN; this is the
  coverage view of the same failure).
- ignored: a RED or UNKNOWN reading unchanged for longer than ``max(2 * max_age,
  1800)`` — no wake, outcome or state change inside the bound.
- unheard: a signal file on disk with no registry entry — nobody subscribes. Reported
  in the detail, never a RED cause by itself (legacy names age out as files).
- failing-producer: a failed ``mishe-producer-*`` user unit (systemctl --user
  evidence; an unreadable systemctl is reported, never assumed empty).
- self: the previous signal-coverage observation stamp older than its own max_age —
  this producer missed a cadence too, and the check names itself.

Exit 0 GREEN, 2 RED, 3 UNKNOWN (registry or manifest unreadable — coverage cannot be
judged, and a coverage check must not guess). Alerts only on RED or UNKNOWN: the duty
registers ``wake_on: "state"``, so GREEN coverage never wakes a subscriber.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

GREEN, RED, UNKNOWN = 0, 2, 3
SELF = "signal-coverage"
RESPONSE_MULT = 2     # a RED/UNKNOWN reading unanswered past 2 * max_age is ignored
RESPONSE_MIN = 1800   # ... but never judged quicker than half an hour
UNIT_PREFIX = "mishe-producer-"
RESERVED_FILES = ("registry.json",)   # non-signal json in signals/ besides *.memory.json


def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _seen_at(sigdir: Path, name: str) -> float | None:
    try:
        return (sigdir / f"{name}.seen").stat().st_mtime
    except OSError:
        return None


def _failing_units(now: float) -> tuple[list[str], str | None]:
    """Failed mishe-producer-* user units, or (names, note) when systemctl cannot answer."""
    binary = os.environ.get("MISHE_SYSTEMCTL_BIN", "systemctl")
    try:
        proc = subprocess.run([binary, "--user", "--failed", "--plain", "--no-legend",
                                "--no-pager"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"unit state unmeasured: {exc}"
    if proc.returncode != 0:
        return [], "unit state unmeasured: systemctl exit " + str(proc.returncode)
    names = [line.split()[0] for line in proc.stdout.splitlines()
             if line.strip().startswith(UNIT_PREFIX)]
    return names, None


def _accounts_unmeasured(home: Path, repo: Path) -> tuple[list[str], str | None]:
    """Budget accounts with no duty that names them (covers/name text scan of the
    manifest — the only deterministic mapping there is; absent files are a note, not a
    verdict, because accounts coverage rides on files another duty owns)."""
    budget = _read_json(home / "engine" / "budget.json")
    manifest = (repo / "duties.json")
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"duties.json unreadable: {exc}"
    if not isinstance(budget, dict) or not isinstance(budget.get("accounts"), dict):
        return [], "engine/budget.json unmeasured (owner: budget duties)"
    return sorted(a for a in budget["accounts"] if a not in text), None


def run(home: Path, now: float, *, repo: Path | None = None,
        sigdir: Path | None = None) -> tuple[int, dict]:
    repo = repo or Path(__file__).resolve().parent.parent
    sigdir = sigdir or Path(home) / "signals"
    registry = _read_json(sigdir / "registry.json")
    if not isinstance(registry, dict) or not registry:
        return UNKNOWN, {"verdict": "UNKNOWN", "cause":
                         "signals/registry.json unreadable: coverage cannot be judged"}
    causes: list[str] = []
    counts = {"registered": len(registry), "ok": 0, "unmeasured": 0, "stale": 0,
              "ignored": 0, "unheard": 0, "failing_units": [], "notes": []}
    detail_lines: list[str] = []

    for name, entry in sorted(registry.items()):
        if not isinstance(entry, dict):
            continue
        max_age = entry.get("max_age")
        if not isinstance(max_age, (int, float)) or max_age <= 0:
            counts["notes"].append(f"{name}: registry entry without max_age")
            continue
        reading = _read_json(sigdir / f"{name}.json")
        seen = _seen_at(sigdir, name)
        if reading is None and seen is None:
            registered_at = entry.get("registered_at")
            if isinstance(registered_at, (int, float)) and now > registered_at + max_age:
                counts["unmeasured"] += 1
                causes.append(f"unmeasured: {name} registered but never observed "
                              f"({(now - registered_at) / 3600:.1f} h since registration)")
            else:
                counts["notes"].append(f"{name}: never observed, inside grace")
            continue
        if seen is not None and now - seen > max_age:
            counts["stale"] += 1
            causes.append(f"stale: {name} freshness stamp {(now - seen) / 60:.0f} min old "
                          f"(max_age {max_age / 60:.0f} min) — producer missed cadence")
            continue
        value = reading.get("value") if isinstance(reading, dict) else None
        state = reading.get("state") if isinstance(reading, dict) else None
        observed_at = reading.get("observed_at") if isinstance(reading, dict) else None
        if (value in ("RED", "UNKNOWN") or state not in (None, "OK")) and \
                isinstance(observed_at, (int, float)):
            # Only a state word (RED/UNKNOWN) or a failed observation is owed a
            # response; a drifting reading (footprint, VERIFIED, samples=…) is
            # healthy data, not an alert, whatever its text says.
            bound = max(RESPONSE_MULT * max_age, RESPONSE_MIN)
            if now - observed_at > bound:
                counts["ignored"] += 1
                causes.append(f"ignored: {name} reading {value or state} unchanged "
                             f"{(now - observed_at) / 3600:.1f} h > bound {bound / 3600:.1f} h")
                continue
            counts["notes"].append(f"{name}: {value or state} fresh, response pending")
            continue
        counts["ok"] += 1

    # Signals on disk nobody subscribed to (no registry entry). *.memory.json files are
    # unit memory, not readings; registry.json is not a signal.
    on_disk = {p.name[:-5] for p in sigdir.glob("*.json")
               if p.name != "registry.json" and not p.name.endswith(".memory.json")}
    orphans = sorted(on_disk - set(registry))
    counts["unheard"] = len(orphans)
    if orphans:
        detail_lines.append("unheard (no subscriber): " + ", ".join(orphans[:12])
                            + (" …" if len(orphans) > 12 else ""))

    units, unit_note = _failing_units(now)
    counts["failing_units"] = units
    if unit_note:
        counts["notes"].append(unit_note)
    elif units:
        causes.append("failing-producer units: " + ", ".join(units))

    # Self: the previous own observation past its max_age is this producer failing.
    self_entry = registry.get(SELF)
    self_line = f"self not registered yet (first run)"
    if isinstance(self_entry, dict):
        self_seen = _seen_at(sigdir, SELF)
        self_max = self_entry.get("max_age", 0)
        if self_seen is None:
            self_line = f"self never observed (duty age unknown)"
        else:
            age = now - self_seen
            self_line = f"self stamp {age / 60:.0f} min old (max_age {self_max / 60:.0f} min)"
            if age > self_max:
                causes.append(f"self stale: signal-coverage stamp {age / 60:.0f} min old — "
                              "this producer missed its cadence")

    unmeasured_accounts, account_note = _accounts_unmeasured(Path(home), repo)
    if account_note:
        counts["notes"].append(account_note)
    # First covered case (Ilya DM #303234): budget-window-care.
    budget_case = []
    for probe in ("budget-opencode-go", "budget-claude"):
        entry = registry.get(probe)
        if isinstance(entry, dict):
            reading = _read_json(sigdir / f"{probe}.json") or {}
            subs = ",".join(entry.get("subscribers", [])) or "none"
            budget_case.append(f"{probe}={reading.get('value', 'unmeasured')}"
                               f" (subs {subs}, max_age {entry.get('max_age', 0) / 60:.0f}m)")
    detail_lines.append("budget-window-care: " + ("; ".join(budget_case) or "no budget signal registered"))

    verdict = RED if causes else GREEN
    report = {"verdict": {GREEN: "GREEN", RED: "RED", UNKNOWN: "UNKNOWN"}[verdict],
              "counts": counts, "causes": causes,
              "unmeasured_accounts": unmeasured_accounts, "self": self_line,
              "detail": "\n".join([f"{k}: {v}" for k, v in counts.items()
                                   if k not in ("failing_units", "notes") and v]
                                  + [f"unit {u} failed" for u in units]
                                  + detail_lines + counts["notes"][:6])}
    return verdict, report


def main(argv: list[str] | None = None) -> int:
    here = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default=here, required=here is None, help="site home")
    parser.add_argument("--now", type=float, default=None, help="test override for the epoch clock")
    args = parser.parse_args(argv)
    now = args.now if args.now is not None else time.time()
    code, report = run(Path(args.home), now)
    print(f"signal-coverage: {report['verdict']}"
          + (f": {report['causes'][0]}" if report.get("causes") else ""))
    for cause in report.get("causes", [])[1:]:
        print(f"  cause: {cause}")
    print(report["detail"])
    return code


if __name__ == "__main__":
    sys.exit(main())
