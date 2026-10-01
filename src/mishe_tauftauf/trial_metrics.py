"""Rough event and service measurements; no claim that counts equal productivity."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from .feed import Feed
from .post_check import _save
from .wall import settings
from .records import payload


PREFIXES = {"wakes": "seed wake ", "redeliveries": "seed redeliver ",
    "settlements": "seed yield ", "observations": "seed observation ",
    "addressed_messages": "[dm]", "legacy_task_transitions": "[task-"}


def collect(home: Path, start: datetime, *, end=None) -> dict:
    end = end or datetime.now(timezone.utc)
    entries = [e for e in Feed(home).entries() if start <= datetime.fromisoformat(e.timestamp.replace("Z", "+00:00")) <= end]
    counts = {name: sum(e.body.startswith(prefix) for e in entries) for name, prefix in PREFIXES.items()}
    by_source = dict(Counter(e.source for e in entries))
    by_role = {}
    for name, prefix in PREFIXES.items():
        if name in {"wakes", "redeliveries", "settlements", "observations"}:
            by_role[name] = dict(Counter(e.body.split()[2] for e in entries if e.body.startswith(prefix)))
    reports = []
    for path in (home / "post-checks").glob("*.json"):
        if start.timestamp() <= path.stat().st_mtime <= end.timestamp():
            try:
                reports.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
    units = json.loads((home / "health/services.json").read_text())
    services = {}
    for unit in units:
        try:
            result = subprocess.run(["systemctl", "--user", "show", unit,
                "-p", "ActiveState,SubState,NRestarts,CPUUsageNSec,MemoryCurrent"], capture_output=True, text=True, timeout=5)
            services[unit] = dict(row.split("=", 1) for row in result.stdout.splitlines() if "=" in row) if result.returncode == 0 else {"unknown": result.stderr.strip()}
        except (OSError, subprocess.SubprocessError) as exc:
            services[unit] = {"unknown": str(exc)}
    outcomes, unavailable, references = Counter(), 0, []
    for entry in entries:
        if entry.body.startswith("Wall outcome "):
            try:
                item = payload(entry)
                evidence = item["evidence"]
                path = Path(evidence["path"])
                if not path.resolve().is_relative_to(home.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
                    raise ValueError("outcome evidence missing or changed")
                outcomes[item["kind"]] += 1
                references.append({"sequence": entry.sequence, "role": entry.source, "kind": item["kind"], "evidence": evidence})
            except (OSError, ValueError, KeyError, TypeError):
                unavailable += 1
    patches = {}
    verified, incomplete, latency = [], [], {}
    for path in (home / "patches").glob("*.json"):
        try:
            record = json.loads(path.read_text())
            patches[path.stem] = record["phase"]
            if record["phase"] == "applied":
                if record.get("delivery_verified") and record.get("verification"):
                    at = datetime.fromisoformat(record["verification"]["at"])
                    if start <= at <= end:
                        verified.append(path.stem)
                        if record.get("prepared_at"):
                            latency[path.stem] = (at - datetime.fromisoformat(record["prepared_at"])).total_seconds()
                else:
                    incomplete.append(path.stem)
        except (OSError, ValueError, KeyError):
            patches[path.stem] = "unknown"
    return {"start": start.isoformat(), "end": end.isoformat(), "hours": (end-start).total_seconds()/3600,
        "entries": len(entries), "counts": counts, "by_source": by_source, "by_role": by_role,
        "post_check_reports": len(reports), "post_check_refused": sum(r.get("clear") is False for r in reports),
        "services": services, "patch_phases": patches,
        "outcomes": {"reported": dict(outcomes), "evidence_unavailable": unavailable, "references": references},
        "deliveries": {"verified_in_window": verified, "applied_without_verification": incomplete, "latency_seconds": latency},
        "model_cost": {"state": "UNKNOWN", "reason": "No complete attributed model usage/cost source is wired; service CPU is not model cost."},
        "limits": "Report mtimes proxy check times. Service counters need start/end deltas and may reset. Supervisor CPU excludes tmux minds and model compute. Settlements and messages are not productivity or verified deliveries. Outcomes are author reports with digest-checked evidence references, not independent semantic acceptance. Patch verification covers the saved scoped delivery exercise, not permanent health. Trial has an added docs role; compare per role and elapsed time."}


def report(home, directory, sample, initial):
    baseline = json.loads((directory / "baseline.json").read_text())
    lines = ["# Wall trial — rough observations", "", f"Measured {sample['hours']:.2f} hours; ending {sample['end']}.", "",
        "| Count | Prior 10 hours | Trial so far |", "| --- | ---: | ---: |"]
    for name in ("wakes", "redeliveries", "settlements", "observations", "addressed_messages"):
        old = baseline["counts"].get("yields" if name == "settlements" else name, 0)
        lines.append(f"| {name.replace('_', ' ')} | {old} | {sample['counts'][name]} |")
    lines += [f"| publication check reports | {baseline['post_check_reports']} | {sample['post_check_reports']} |",
        f"| refused publication reports | {baseline['post_check_refused']} | {sample['post_check_refused']} |", "", "Service restart deltas:", ""]
    for unit, values in sample["services"].items():
        before = initial["services"].get(unit, {}).get("NRestarts")
        after = values.get("NRestarts")
        delta = int(after)-int(before) if before and after and before.isdigit() and after.isdigit() else None
        lines.append(f"- {unit}: {delta if delta is not None and delta >= 0 else 'unknown/reset'}; {values.get('ActiveState', 'unknown')}/{values.get('SubState', 'unknown')}")
    lines += ["", "Evidence-backed outcomes (author reports):", ""]
    for kind, count in sample["outcomes"]["reported"].items():
        lines.append(f"- {kind}: {count}")
    if not sample["outcomes"]["reported"]:
        lines.append("- No evidenced outcome reports in this window; absence of reports is not proof of no useful work.")
    lines += [f"- Missing or changed outcome evidence: {sample['outcomes']['evidence_unavailable']}",
              "", f"Verified scoped deliveries in window: {len(sample['deliveries']['verified_in_window'])}.",
              "Applied without complete recorded verification: " + (", ".join(sample["deliveries"]["applied_without_verification"]) or "none") + ".",
              "Delivery latency seconds: " + json.dumps(sample["deliveries"]["latency_seconds"], sort_keys=True) + ".",
              "Model cost: UNKNOWN — no complete attributed source wired."]
    lines += ["", "These are diagnostic counts. Inspect walls, representative turns and live patch evidence before judging useful work.", "", sample["limits"], "",
        "Final means the ten-hour sampling window ended; it does not certify all in-flight work completed."]
    (directory / "report.md").write_text("\n".join(lines)+"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=300)
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("interval must be positive")
    config = settings(args.home)
    start = datetime.fromisoformat(config["started"])
    until = datetime.fromisoformat(config["until"])
    args.directory.mkdir(parents=True, exist_ok=True)
    initial_path = args.directory / "initial.json"
    initial = json.loads(initial_path.read_text()) if initial_path.exists() else collect(args.home, start)
    _save(initial_path, initial)
    while True:
        now = datetime.now(timezone.utc)
        sample = collect(args.home, start, end=min(now, until))
        _save(args.directory / "latest.json", sample)
        _save(args.directory / ("sample-"+str(int(now.timestamp()))+".json"), sample)
        report(args.home, args.directory, sample, initial)
        print(f"sample {sample['hours']:.2f}h entries={sample['entries']} wakes={sample['counts']['wakes']}", flush=True)
        if now >= until:
            _save(args.directory / "final.json", sample)
            break
        if not args.watch:
            break
        time.sleep(min(args.interval, max(0.01, (until-now).total_seconds())))


if __name__ == "__main__":
    main()
