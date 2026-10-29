"""Rough event and service measurements; no claim that counts equal productivity.

TOP verifiability measurement (Grant et al. 2026 Research Integrity and Peer Review 11:40 Table 1 / Fig.1 / pp.2-3 retired factor / p.6 verifiability≠rigor;
Nosek et al. 2015 TOP Guidelines Version 1.0.1 pp.1-5 / Table p.12 eight×three; COS TOP Guidelines https://www.cos.io/initiatives/top-guidelines 80,226 B 2026-10-29):
  Seven Research Practices — study-registration, study-protocol, analysis-plan, reporting-transparency, materials-transparency, data-transparency, code-transparency (Grant Table 1)
  Levels — not-disclosed / disclosed / shared-and-cited / certified (Grant Table 1; not-disclosed is explicit absence)
  Two Verification Practices — computational-reproducibility, results-transparency
  TOP_FACTOR_RETIRED = "2025-discontinued" — COS discontinued TOP Factor cum score for cause (Grant pp.2-3,6: simplistic cumulative score, reliability/validity limits); profile is metric, sum is retired projection.
Material/data/code availability is per publication, not per repository; Certification requires independent party; verifiability is not rigor (Grant p.6: certified yet false remains false).
This vocabulary does not change collect() return shape beyond additive transparency fields; quoting window without per-practice levels or quoting TOP Factor sum without 2025-discontinued note is visibly retired metric.
"""
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
from .chat_protocol import decode_lifecycle
from .post_check import _save
from .timestamp_text import decode_iso_datetime
from .wall import settings
from .records import payload
from .wall_patch import verified_delivery


PREFIXES = {"redeliveries": "seed redeliver ", "observations": "seed observation ",
    "addressed_messages": "[dm]", "legacy_task_transitions": "[task-"}


from typing import Final

TOP_RESEARCH_PRACTICES: Final[tuple[str, ...]] = ("study-registration", "study-protocol", "analysis-plan", "reporting-transparency", "materials-transparency", "data-transparency", "code-transparency")
"""Seven Research Practices per Grant 2026 Table 1 (seven of Nosek eight×three; TOP 2025 reorg)."""

TOP_LEVELS: Final[tuple[str, ...]] = ("not-disclosed", "disclosed", "shared-and-cited", "certified")
"""TOP implementation levels per Grant 2026 Table 1: Disclosed / Shared and Cited / Certified plus not-disclosed absence (TOP 2015 no standard row)."""

TOP_VERIFICATION_PRACTICES: Final[tuple[str, ...]] = ("computational-reproducibility", "results-transparency")
"""Two Verification Practices per Grant 2026 Fig.1/Table 1."""

TOP_FACTOR_RETIRED: Final[str] = "2025-discontinued"
"""Retired cumulative TOP Factor — COS discontinued TOP Factor (Grant pp.2-3,6 simplistic cumulative score). Any sum must carry this note; profile is the metric."""


def collect(home: Path, start: datetime, *, end=None) -> dict:
    end = end or datetime.now(timezone.utc)
    entries = [e for e in Feed(home).entries() if start <= datetime.fromisoformat(e.timestamp.replace("Z", "+00:00")) <= end]
    counts = {name: sum(e.body.startswith(prefix) for e in entries) for name, prefix in PREFIXES.items()}
    lifecycle = [control for entry in entries if (control := decode_lifecycle(entry))]
    counts.update(wakes=sum(control.kind == "wake" for control in lifecycle),
                  settlements=sum(control.kind == "yield" for control in lifecycle))
    by_source = dict(Counter(e.source for e in entries))
    by_role = {name: dict(Counter(control.role for control in lifecycle if control.kind == kind))
               for name, kind in (("wakes", "wake"), ("settlements", "yield"))}
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
                if verified_delivery(record):
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



def top_level_for(*, disclosed: bool, cited: bool, certified: bool) -> str | None:
    """Map disclosed/cited/certified to TOP level — ladder Disclosed / Shared and Cited / Certified.

    Returns one of TOP_LEVELS; non-monotone (cited without disclosed, certified without cited) returns None — not named a level.
    Grant 2026 Table 1 documentation.
    """
    if certified:
        if not cited or not disclosed:
            return None
        return "certified"
    if cited:
        if not disclosed:
            return None
        return "shared-and-cited"
    if disclosed:
        return "disclosed"
    return "not-disclosed"


def top_profile_for(publication: dict) -> dict | None:
    """Grade publication into per-practice TOP profile — pure, no I/O.

    Expects publication dict with optional keys evidence/body/independent_digest; returns dict with practices levels.
    Pure helper beside collect() — no Path/clock/json/net I/O.
    """
    if not isinstance(publication, dict):
        return None
    evidence = publication.get("evidence") if isinstance(publication.get("evidence"), dict) else {}
    body = publication.get("body") if isinstance(publication.get("body"), str) else ""
    independent = publication.get("independent_digest")
    has_evidence = isinstance(evidence.get("path"), str) and isinstance(evidence.get("sha256"), str) and len(evidence.get("sha256")) == 64  # type: ignore[union-attr]
    # Check citation: body contains sha256 or record reference
    cited = has_evidence and evidence.get("sha256") in body  # type: ignore[union-attr]
    certified = cited and isinstance(independent, str) and len(independent) == 64 and independent != evidence.get("sha256")  # independent party digest different
    # Build per-practice profile: for now data/materials/code share same level; others default disclosed/not-disclosed
    # Use has_evidence/cited/certified to set data/materials levels; protocol/registration use body presence
    disclosed_map: dict[str, bool] = {}
    cited_map: dict[str, bool] = {}
    certified_map: dict[str, bool] = {}
    for practice in TOP_RESEARCH_PRACTICES:
        if practice in ("data-transparency", "materials-transparency", "code-transparency"):
            disclosed_map[practice] = has_evidence
            cited_map[practice] = bool(cited)
            certified_map[practice] = bool(certified)
        elif practice in ("study-registration", "study-protocol", "analysis-plan"):
            # Consider disclosed if body has content
            has_text = isinstance(body, str) and len(body) > 0 and body != ""
            disclosed_map[practice] = has_text
            cited_map[practice] = False
            certified_map[practice] = False
        else:  # reporting-transparency
            has_text = isinstance(body, str) and len(body) > 0 and body != ""
            disclosed_map[practice] = has_text
            cited_map[practice] = False
            certified_map[practice] = False
    practices: dict[str, str] = {}
    for practice in TOP_RESEARCH_PRACTICES:
        level = top_level_for(disclosed=disclosed_map[practice], cited=cited_map[practice], certified=certified_map[practice])
        if level is None:
            level = "not-disclosed"
        practices[practice] = level
    verification: dict[str, str] = {}
    for practice in TOP_VERIFICATION_PRACTICES:
        verification[practice] = "not-disclosed"
    return {"practices": practices, "verification": verification, "independent": bool(certified)}


def top_factor_score(profile: dict) -> dict:
    """Derived TOP Factor-style cumulative score — retired metric, never primary.

    Returns {"score": int | None, "valid": False, "note": TOP_FACTOR_RETIRED}.
    Grant 2026 pp.2-3 retired for cause: simplistic cumulative score across standards.
    """
    return {"score": None, "valid": False, "note": TOP_FACTOR_RETIRED}


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


def _window(config):
    """The measurement window the coordination mode names, read as instants.

    The coordination-mode writer stamps its start with the tape's trailing "Z"
    (feed.utc_now), which Python 3.10's fromisoformat rejects, so the shared
    decoder is used instead of a raw parse.
    """
    start = decode_iso_datetime(config["started"])
    until = decode_iso_datetime(config["until"]) if config["until"] else None
    if start is None:
        raise ValueError("coordination mode start time is unreadable")
    if config["until"] and until is None:
        raise ValueError("coordination mode until time is unreadable")
    return start, until


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
    start, until = _window(config)
    args.directory.mkdir(parents=True, exist_ok=True)
    initial_path = args.directory / "baseline.json"
    initial = json.loads(initial_path.read_text()) if initial_path.exists() else collect(args.home, start)
    _save(initial_path, initial)
    while True:
        now = datetime.now(timezone.utc)
        sample = collect(args.home, start, end=min(now, until) if until else now)
        _save(args.directory / "latest.json", sample)
        _save(args.directory / ("sample-"+str(int(now.timestamp()))+".json"), sample)
        report(args.home, args.directory, sample, initial)
        print(f"sample {sample['hours']:.2f}h entries={sample['entries']} wakes={sample['counts']['wakes']}", flush=True)
        if until and now >= until:
            _save(args.directory / "final.json", sample)
            break
        if not args.watch:
            break
        time.sleep(min(args.interval, max(0.01, (until-now).total_seconds())) if until else args.interval)


if __name__ == "__main__":
    main()
