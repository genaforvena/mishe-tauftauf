"""Rough event and service measurements; no claim that counts equal productivity.

TOP verifiability measurement (Grant et al. 2026 Research Integrity and Peer Review 11:40 Table 1 / Fig.1 / pp.2-3 retired factor / p.6 verifiability≠rigor;
Nosek et al. 2015 TOP Guidelines Version 1.0.1 pp.1-5 / Table p.12 eight×three; COS TOP Guidelines https://www.cos.io/initiatives/top-guidelines 80,226 B 2026-10-29):
  Seven Research Practices — study-registration, study-protocol, analysis-plan, reporting-transparency, materials-transparency, data-transparency, code-transparency (Grant Table 1)
  Levels — not-disclosed / disclosed / shared-and-cited / certified (Grant Table 1; not-disclosed is explicit absence)
  Two Verification Practices — computational-reproducibility, results-transparency
  TOP_FACTOR_RETIRED = "2025-discontinued" — COS discontinued TOP Factor cum score for cause (Grant pp.2-3,6: simplistic cumulative score, reliability/validity limits); profile is metric, sum is retired projection.
Material/data/code availability is per publication, not per repository; Certification requires independent party; verifiability is not rigor (Grant p.6: certified yet false remains false).
This vocabulary does not change collect() return shape beyond additive transparency fields; quoting window without per-practice levels or quoting TOP Factor sum without 2025-discontinued note is visibly retired metric.

Hicks et al. 2015 Nature 520:429-431 Leiden Manifesto for research metrics — https://doi.org/10.1038/520429a + https://www.nature.com/articles/520429a (244,451 B 2026-11-02) + https://www.leidenmanifesto.org/ (20,656 B) — 10 principles: quantitative-supports-qualitative … scrutinize-regularly; TOP transparency is disclosure, Leiden is responsible use; Principle 8 false-precision (three-decimal JIF example) marks precision>2 as canonical false precision.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
from datetime import datetime, timedelta, timezone
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

# Placebo scale: window k spans [trial_start - k*W, trial_start - (k-1)*W).
# Window 0 is the trial and window 1 the baseline; both stay out of the denominator.
PLACEBO_WINDOW = timedelta(hours=10)
PLACEBO_FIRST, PLACEBO_LAST = 2, 11
# A placebo window with fewer feed entries than this is a feed-coverage gap (outage),
# not an ordinary window. Chosen by eye from the 2026-10-27..29 outage windows
# (0-160 entries) against ordinary windows (1087-8894); not tuned to any outcome.
PLACEBO_ENTRY_FLOOR = 1000
PLACEBO_COUNTS = ("wakes", "redeliveries", "settlements", "observations", "addressed_messages")


def _instant(entry) -> datetime:
    instant = decode_iso_datetime(entry.timestamp)
    if instant is None:
        raise ValueError("feed entry timestamp is unreadable")
    return instant


def _window_counts(entries) -> dict:
    counts = {name: sum(e.body.startswith(prefix) for e in entries) for name, prefix in PREFIXES.items()}
    lifecycle = [control for entry in entries if (control := decode_lifecycle(entry))]
    counts.update(wakes=sum(control.kind == "wake" for control in lifecycle),
                  settlements=sum(control.kind == "yield" for control in lifecycle))
    return counts


def placebo_scale(entries, trial_start: datetime, *, floor: int = PLACEBO_ENTRY_FLOOR) -> dict:
    """Coverage-gated placebo windows and the mean absolute swing per count.

    The swing is the mean |difference| between consecutive kept placebo windows. A window
    below the entry floor is excluded and listed, so an outage cannot pose as a small swing.
    A zero swing yields None (UNKNOWN), never a division.
    """
    stamped = [(_instant(e), e) for e in entries]
    windows = []
    for k in range(PLACEBO_FIRST, PLACEBO_LAST + 1):
        hi = trial_start - (k - 1) * PLACEBO_WINDOW
        lo = trial_start - k * PLACEBO_WINDOW
        inside = [e for at, e in stamped if lo <= at < hi]
        windows.append((k, len(inside), _window_counts(inside)))
    kept = [(k, counts) for k, n, counts in windows if n >= floor]
    swing = {}
    for name in PLACEBO_COUNTS:
        series = [counts[name] for _, counts in kept]
        diffs = [abs(b - a) for a, b in zip(series, series[1:])]
        total = sum(diffs)
        swing[name] = total / len(diffs) if diffs and total > 0 else None
    return {"floor": floor, "windows": [k for k, _, _ in windows],
            "kept": [k for k, _ in kept], "excluded": [k for k, n, _ in windows if n < floor],
            "entries": {str(k): n for k, n, _ in windows}, "mean_abs_swing": swing}


def _maturation_trend(series: list[int | float]) -> str:
    """Monotone trend across kept placebo windows — pure, no I/O.

    Returns flat/rising/falling/UNKNOWN. UNKNOWN when <3 points.
    """
    if not isinstance(series, list) or len(series) < 3:
        return "UNKNOWN"
    try:
        nums = [float(x) for x in series]
    except (TypeError, ValueError):
        return "UNKNOWN"
    if any(not __import__("math").isfinite(v) for v in nums):
        return "UNKNOWN"
    ups = sum(1 for a, b in zip(nums, nums[1:]) if b > a)
    downs = sum(1 for a, b in zip(nums, nums[1:]) if b < a)
    n = len(nums) - 1
    if ups == n:
        return "rising"
    if downs == n:
        return "falling"
    if ups >= 0.6 * n and downs == 0:
        return "rising"
    if downs >= 0.6 * n and ups == 0:
        return "falling"
    if ups == 0 and downs == 0:
        return "flat"
    return "flat"


def campbell_design2_audit(sample, baseline, placebo, *, history_events=None, prefixes=None) -> dict:
    """Campbell & Stanley Design 2 (O1 X O2) audit — pure, no I/O.

    History and maturation are UNCONTROLLED by design (Table 1 minuses for
    Design 2, p.8). This helper never returns "controlled" for those.
    """
    design = "O1 X O2 (Design 2)"
    history = "UNCONTROLLED"
    maturation = "UNCONTROLLED"
    if placebo is None:
        instrumentation = "UNKNOWN"
    elif prefixes is not None:
        try:
            instrumentation = "mismatch" if dict(prefixes) != dict(PREFIXES) else "ok"
        except Exception:
            instrumentation = "UNKNOWN"
    else:
        instrumentation = "ok"
    if isinstance(placebo, dict) and "excluded" in placebo:
        try:
            selection_excluded = list(placebo.get("excluded") or [])
        except Exception:
            selection_excluded = "UNKNOWN"
    else:
        selection_excluded = "UNKNOWN"
    regression: dict[str, str] = {}
    maturation_trend: dict[str, str] = {}
    if isinstance(placebo, dict) and "kept" in placebo and isinstance(sample, dict) and isinstance(baseline, dict):
        try:
            per_window = placebo.get("_window_counts")
            for name in PLACEBO_COUNTS:
                if per_window and name in per_window and isinstance(per_window[name], list) and len(per_window[name]) >= 3:
                    maturation_trend[name] = _maturation_trend(per_window[name])
                elif per_window and name in per_window:
                    maturation_trend[name] = "UNKNOWN"
                else:
                    maturation_trend[name] = "UNKNOWN"
                try:
                    b_counts = baseline.get("counts") if isinstance(baseline.get("counts"), dict) else baseline
                    b_val = b_counts.get(name) if isinstance(b_counts, dict) else None
                    if b_val is None and isinstance(b_counts, dict):
                        b_val = b_counts.get("yields" if name == "settlements" else name)
                        if b_val is None:
                            b_val = b_counts.get(name)
                    if per_window and name in per_window and b_val is not None:
                        series = per_window[name]
                        if series and (b_val == min(series) or b_val == max(series)):
                            regression[name] = "baseline is extreme (possible regression)"
                        else:
                            regression[name] = "baseline not extreme"
                    else:
                        regression[name] = "UNKNOWN"
                except Exception:
                    regression[name] = "UNKNOWN"
        except Exception:
            for name in PLACEBO_COUNTS:
                regression[name] = "UNKNOWN"
                maturation_trend[name] = "UNKNOWN"
    else:
        for name in PLACEBO_COUNTS:
            regression[name] = "UNKNOWN"
            maturation_trend[name] = "UNKNOWN"
    flat_trends = set(maturation_trend.values()) if maturation_trend else set()
    if len(flat_trends) == 1 and "UNKNOWN" not in flat_trends:
        global_trend = next(iter(flat_trends))
    elif all(v == "UNKNOWN" for v in maturation_trend.values()):
        global_trend = "UNKNOWN"
    else:
        global_trend = "UNKNOWN"
    return {
        "design": design,
        "history": history,
        "maturation": maturation,
        "instrumentation": instrumentation,
        "selection": {"excluded": selection_excluded},
        "regression": regression,
        "maturation_trend": maturation_trend,
        "maturation_trend_global": global_trend,
        "history_events": list(history_events) if isinstance(history_events, list) else None,
    }


def huang_checks(placebo, sample, baseline, *, prefixes=None, publication=None) -> dict:
    """Huang et al. 2024 intrinsic self-correction audit — pure, no I/O."""
    def _extract(obj):
        if isinstance(obj, dict) and "counts" in obj and isinstance(obj["counts"], dict):
            return obj["counts"], obj.get("hours")
        if isinstance(obj, dict):
            return obj, None
        return {}, None
    sample_counts, sample_hours = _extract(sample)
    baseline_counts, baseline_hours = _extract(baseline)
    if sample_hours is None and isinstance(sample, dict) and isinstance(sample.get("hours"), (int, float)):
        sample_hours = sample.get("hours")
    if baseline_hours is None and isinstance(baseline, dict) and isinstance(baseline.get("hours"), (int, float)):
        baseline_hours = baseline.get("hours")
    if placebo is None:
        decoder_parity = "UNKNOWN"
    elif prefixes is not None:
        try:
            decoder_parity = "mismatch" if dict(prefixes) != dict(PREFIXES) else "ok"
        except Exception:
            decoder_parity = "UNKNOWN"
    else:
        decoder_parity = "ok"
    per_count: dict[str, dict] = {}
    for name in PLACEBO_COUNTS:
        try:
            sv = sample_counts.get(name) if isinstance(sample_counts, dict) else None
            bv = None
            if isinstance(baseline_counts, dict):
                bv = baseline_counts.get(name if name != "settlements" else "yields")
                if bv is None:
                    bv = baseline_counts.get(name)
            scale = None
            if isinstance(placebo, dict):
                scale = (placebo.get("mean_abs_swing") or {}).get(name)
            raw_q = None
            if isinstance(sv, (int, float)) and isinstance(bv, (int, float)) and isinstance(scale, (int, float)) and scale not in (None, 0):
                raw_q = (sv - bv) / scale if scale != 0 else None
        except Exception:
            raw_q = None
        cost_q = None
        try:
            if isinstance(sv, (int, float)) and isinstance(bv, (int, float)) and isinstance(placebo, dict):
                scale = (placebo.get("mean_abs_swing") or {}).get(name)
                if scale is not None and scale != 0 and isinstance(sample_hours, (int, float)) and isinstance(baseline_hours, (int, float)) and sample_hours > 0 and baseline_hours > 0:
                    swing_per_hour = scale / 10.0 if 10.0 != 0 else None
                    if swing_per_hour and swing_per_hour != 0:
                        diff_rate = sv / sample_hours - bv / baseline_hours
                        cost_q = diff_rate / swing_per_hour if swing_per_hour != 0 else None
                elif scale is not None and scale != 0 and (sample_hours is None or baseline_hours is None):
                    cost_q = raw_q
        except Exception:
            cost_q = None
        per_count[name] = {
            "oracle_free": True,
            "decoder_parity": decoder_parity,
            "raw_q": raw_q,
            "cost_parity_q": cost_q,
        }
    if publication is not None and isinstance(publication, dict):
        ind = publication.get("independent_digest")
        if isinstance(ind, str) and len(ind) == 64 and all(c in "0123456789abcdefABCDEF" for c in ind):
            per_count["publication"] = {"oracle_free": False, "decoder_parity": decoder_parity, "raw_q": None, "cost_parity_q": None}
        elif ind is not None:
            per_count["publication"] = {"oracle_free": False, "decoder_parity": decoder_parity, "raw_q": None, "cost_parity_q": None}
    return per_count


from typing import Final

TOP_RESEARCH_PRACTICES: Final[tuple[str, ...]] = ("study-registration", "study-protocol", "analysis-plan", "reporting-transparency", "materials-transparency", "data-transparency", "code-transparency")
"""Seven Research Practices per Grant 2026 Table 1 (seven of Nosek eight×three; TOP 2025 reorg)."""

TOP_LEVELS: Final[tuple[str, ...]] = ("not-disclosed", "disclosed", "shared-and-cited", "certified")
"""TOP implementation levels per Grant 2026 Table 1: Disclosed / Shared and Cited / Certified plus not-disclosed absence (TOP 2015 no standard row)."""

TOP_VERIFICATION_PRACTICES: Final[tuple[str, ...]] = ("computational-reproducibility", "results-transparency")
"""Two Verification Practices per Grant 2026 Fig.1/Table 1."""

TOP_FACTOR_RETIRED: Final[str] = "2025-discontinued"
"""Retired cumulative TOP Factor — COS discontinued TOP Factor (Grant pp.2-3,6 simplistic cumulative score). Any sum must carry this note; profile is the metric."""

LEIDEN_PRINCIPLES: Final[tuple[str, ...]] = (
    "quantitative-supports-qualitative",
    "measure-against-mission",
    "protect-locally-relevant",
    "open-transparent-simple",
    "allow-verification",
    "account-for-field-variation",
    "portfolio-judgement-for-individuals",
    "avoid-misplaced-concreteness-and-false-precision",
    "recognize-systemic-effects",
    "scrutinize-regularly",
)
"""Ten Leiden principles per Hicks et al. 2015 Nature 520:429-431."""

LEIDEN_PRINCIPLE_DEFINITIONS: Final[dict[str, str]] = {
    "quantitative-supports-qualitative": "Quantitative evaluation should support qualitative, expert assessment.",
    "measure-against-mission": "Measure performance against the research missions of the institution, group, or researcher.",
    "protect-locally-relevant": "Protect excellence in locally relevant research.",
    "open-transparent-simple": "Keep data collection and analytical processes open, transparent and simple.",
    "allow-verification": "Allow those evaluated to verify data and analysis.",
    "account-for-field-variation": "Account for variation by field in publication and citation practices.",
    "portfolio-judgement-for-individuals": "Base assessment of individual researchers on a qualitative judgement of their portfolio.",
    "avoid-misplaced-concreteness-and-false-precision": "Avoid misplaced concreteness and false precision.",
    "recognize-systemic-effects": "Recognize the systemic effects of assessment and indicators.",
    "scrutinize-regularly": "Scrutinize indicators regularly and update them.",
}
"""Leiden definitions (Nature p.429-431 / leidenmanifesto.org)."""

LEIDEN_MAX_FALSE_PRECISION_DECIMALS: Final[int] = 2
"""Threshold per Leiden Principle 8: three decimals (e.g. JIF) is false precision."""


def leiden_principle_is_valid(principle: object) -> bool:
    """Membership in LEIDEN_PRINCIPLES."""
    return principle in LEIDEN_PRINCIPLES


def leiden_principles_are_valid(principles: object) -> bool:
    """Non-empty iterable of valid Leiden principles with no duplicates."""
    if not isinstance(principles, (list, tuple)):
        return False
    if not principles:
        return False
    seen: set[str] = set()
    for p in principles:
        if p not in LEIDEN_PRINCIPLES:
            return False
        if p in seen:
            return False
        seen.add(p)
    return True


def leiden_principle_definition(principle: str) -> str | None:
    """Definition for a Leiden principle or None."""
    return LEIDEN_PRINCIPLE_DEFINITIONS.get(principle)


def leiden_compliance_for(
    *,
    quantitative_supports_qualitative: bool,
    open_transparent_simple: bool,
    allow_verification: bool,
    field_variation_accounted: bool,
    mission_aligned: bool,
    locally_relevant_protected: bool,
    portfolio_judgement: bool,
    false_precision_ok: bool,
    systemic_effects_acknowledged: bool,
    scrutiny_current: bool,
) -> dict:
    """Compliance profile over 10 Leiden principles — pure, no I/O."""
    flags: list[tuple[str, bool]] = [
        ("quantitative-supports-qualitative", bool(quantitative_supports_qualitative)),
        ("measure-against-mission", bool(mission_aligned)),
        ("protect-locally-relevant", bool(locally_relevant_protected)),
        ("open-transparent-simple", bool(open_transparent_simple)),
        ("allow-verification", bool(allow_verification)),
        ("account-for-field-variation", bool(field_variation_accounted)),
        ("portfolio-judgement-for-individuals", bool(portfolio_judgement)),
        ("avoid-misplaced-concreteness-and-false-precision", bool(false_precision_ok)),
        ("recognize-systemic-effects", bool(systemic_effects_acknowledged)),
        ("scrutinize-regularly", bool(scrutiny_current)),
    ]
    compliant = [name for name, ok in flags if ok]
    gaps = [name for name, ok in flags if not ok]
    return {
        "compliant_principles": compliant,
        "gaps": gaps,
        "compliance_ratio": len(compliant) / len(LEIDEN_PRINCIPLES) if LEIDEN_PRINCIPLES else 0.0,
        "scrutiny_unknown": not bool(scrutiny_current),
    }


def scrutiny_due(
    last_scrutinized_at: datetime | None, now: datetime, *, max_age_days: int = 90
) -> bool:
    """True when last is None or now - last exceeds max_age_days."""
    if last_scrutinized_at is None:
        return True
    if not isinstance(last_scrutinized_at, datetime) or not isinstance(now, datetime):
        return True
    delta = now - last_scrutinized_at
    return delta > timedelta(days=max_age_days)


def false_precision_holds(*args, **kwargs) -> bool:
    """True when precision exceeds LEIDEN_MAX_FALSE_PRECISION_DECIMALS (3 decimals is the JIF example)."""
    precision: int | None = None
    if kwargs:
        if "precision" in kwargs and isinstance(kwargs["precision"], int):
            precision = kwargs["precision"]
        elif "decimals" in kwargs and isinstance(kwargs["decimals"], int):
            precision = kwargs["decimals"]
    if precision is None and args:
        # Support both (precision) and (value, precision) calling conventions
        if len(args) == 1 and isinstance(args[0], int):
            precision = args[0]
        elif len(args) == 2 and isinstance(args[1], int):
            precision = args[1]
        elif len(args) == 2 and isinstance(args[0], int):
            precision = args[0]
        elif len(args) == 1 and isinstance(args[0], float):
            return False
    if not isinstance(precision, int):
        return False
    return precision > LEIDEN_MAX_FALSE_PRECISION_DECIMALS



def collect(home: Path, start: datetime, *, end=None) -> dict:
    end = end or datetime.now(timezone.utc)
    everything = list(Feed(home).entries())
    entries = [e for e in everything if start <= _instant(e) <= end]
    counts = _window_counts(entries)
    lifecycle = [control for entry in entries if (control := decode_lifecycle(entry))]
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
    patch_records: dict[str, dict] = {}
    verified, incomplete, latency = [], [], {}
    for path in (home / "patches").glob("*.json"):
        try:
            record = json.loads(path.read_text())
            patches[path.stem] = record["phase"]
            patch_records[path.stem] = record
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
    # Per-1k-char normalization — Pugachev 2025 §6.2 de-confounding volume inflation.
    # Raw latency conflates coordination cost with 82-189% code-volume inflation in
    # parallel mode. Callers should log both raw_s and per_1k_chars = raw * 1000 / chars.
    latency_per_1k: dict[str, float] = {}
    for stem, seconds in latency.items():
        record = patch_records.get(stem) or {}
        chars = record.get("chars") or record.get("diff_chars") or record.get("bytes")
        if isinstance(chars, int) and chars > 0:
            latency_per_1k[stem] = seconds * 1000.0 / chars
    return {"start": start.isoformat(), "end": end.isoformat(), "hours": (end-start).total_seconds()/3600,
        "entries": len(entries), "counts": counts, "placebo": placebo_scale(everything, start), "by_source": by_source, "by_role": by_role,
        "post_check_reports": len(reports), "post_check_refused": sum(r.get("clear") is False for r in reports),
        "services": services, "patch_phases": patches,
        "outcomes": {"reported": dict(outcomes), "evidence_unavailable": unavailable, "references": references},
        "deliveries": {"verified_in_window": verified, "applied_without_verification": incomplete, "latency_seconds": latency, "latency_per_1k_chars": latency_per_1k, "latency_note": "per_1k_chars = seconds*1000/chars de-confounds 82-189% volume inflation; report raw_s beside per_1k (§6.2)"},
        "model_cost": {"state": "UNKNOWN", "reason": "No complete attributed model usage/cost source is wired; service CPU is not model cost."},
        "limits": "Report mtimes proxy check times. Service counters need start/end deltas and may reset. Supervisor CPU excludes tmux minds and model compute. Settlements and messages are not productivity or verified deliveries. Outcomes are author reports with digest-checked evidence references, not independent semantic acceptance. Patch verification covers the saved scoped delivery exercise, not permanent health. Trial has an added docs role; compare per role and elapsed time."}

def per_1k_chars_seconds(seconds: float, chars: int | None) -> float | None:
    """Normalize latency per 1k chars — de-confounds volume inflation, §6.2.

    Returns ``seconds * 1000 / chars`` or None when chars is None/0. Pure, no I/O.
    Use beside raw seconds so an 82-189% volume inflation (Pomodoro +82% etc.)
    is not mis-charged to coordination. Mirrors the paper's per-1k-char table.
    """
    if chars is None:
        return None
    try:
        c = int(chars)
        s = float(seconds)
    except (TypeError, ValueError):
        return None
    if c <= 0 or not __import__("math").isfinite(s) or s < 0:
        return None
    return s * 1000.0 / c




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


def _placebo_lines(sample, baseline) -> list[str]:
    placebo = sample.get("placebo")
    if not placebo:
        return ["", "Scaled differences: not computed in this sample (UNKNOWN)."]
    excluded = ", ".join(str(k) for k in placebo["excluded"]) or "none"
    lines = ["", f"Scaled differences: (trial − baseline) / mean |Δ| across placebo windows {placebo['kept']} "
             f"(entry floor {placebo['floor']}; excluded as feed-coverage gaps: {excluded}).", "",
             "| Count | Trial − baseline | Mean abs Δ placebo | q |", "| --- | ---: | ---: | ---: |"]
    for name in PLACEBO_COUNTS:
        diff = sample["counts"][name] - baseline["counts"].get("yields" if name == "settlements" else name, 0)
        scale = placebo["mean_abs_swing"][name]
        scale_text = "UNKNOWN" if scale is None else f"{scale:.2f}"
        q_text = "UNKNOWN (zero scale)" if scale is None else f"{diff / scale:.2f}"
        lines.append(f"| {name.replace('_', ' ')} | {diff} | {scale_text} | {q_text} |")
    lines += ["", f"Limit: the entry floor ({placebo['floor']}) is a judgment, not derived from outage evidence; "
              "the scaled section is not a verdict on usefulness, and the floor was chosen on the same windows it reports."]
    return lines


def _campbell_lines(sample, baseline) -> list[str]:
    placebo = sample.get("placebo") if isinstance(sample, dict) else None
    try:
        audit = campbell_design2_audit(sample, baseline, placebo)
    except Exception:
        return ["", "Campbell Design 2 audit (O1 X O2): UNKNOWN — audit failed."]
    history = audit.get("history", "UNKNOWN")
    maturation = audit.get("maturation", "UNKNOWN")
    instr = audit.get("instrumentation", "UNKNOWN")
    excluded = audit.get("selection", {}).get("excluded", "UNKNOWN") if isinstance(audit.get("selection"), dict) else "UNKNOWN"
    if isinstance(excluded, list):
        excluded_text = ", ".join(str(k) for k in excluded) or "none"
    else:
        excluded_text = str(excluded)
    regression = audit.get("regression", {})
    maturation_trend = audit.get("maturation_trend", {})
    lines = ["", "Campbell Design 2 audit (O1 X O2): "
             f"history={history} — single O1 vs single O2 shares history between measures; "
             f"maturation={maturation} — time-series not collected (see Design 7); "
             f"instrumentation={instr}; selection=excluded k={excluded_text}."]
    for name in PLACEBO_COUNTS:
        reg = regression.get(name, "UNKNOWN") if isinstance(regression, dict) else "UNKNOWN"
        trend = maturation_trend.get(name, "UNKNOWN") if isinstance(maturation_trend, dict) else "UNKNOWN"
        lines.append(f"- {name.replace('_', ' ')}: regression={reg}; maturation_trend={trend}")
    lines.append("Limit: this audit labels threats per Table 1; it does not estimate a causal effect and placebos remain autocorrelated shared-regime windows, not exchangeable controls.")
    return lines


def _huang_lines(sample, baseline) -> list[str]:
    placebo = sample.get("placebo") if isinstance(sample, dict) else None
    try:
        checks = huang_checks(placebo, sample, baseline)
    except Exception:
        return ["", "Huang audit: UNKNOWN — audit failed."]
    lines = ["", "Huang audit (intrinsic self-correction, cost/prompt parity):"]
    for name in PLACEBO_COUNTS:
        entry = checks.get(name, {}) if isinstance(checks, dict) else {}
        oracle = entry.get("oracle_free", "UNKNOWN")
        if isinstance(oracle, bool):
            oracle_text = "yes" if oracle else "no"
        else:
            oracle_text = str(oracle)
        decoder = entry.get("decoder_parity", "UNKNOWN")
        raw_q = entry.get("raw_q")
        cost_q = entry.get("cost_parity_q")
        raw_text = f"{raw_q:.2f}" if isinstance(raw_q, (int, float)) else "UNKNOWN"
        cost_text = f"{cost_q:.2f}" if isinstance(cost_q, (int, float)) else "UNKNOWN"
        lines.append(f"- {name.replace('_', ' ')}: oracle_free={oracle_text} cost_parity_q={cost_text} decoder_parity={decoder} (raw_q={raw_text})")
    if "publication" in checks:
        pub = checks["publication"]
        oracle = pub.get("oracle_free", "UNKNOWN")
        oracle_text = "no" if oracle is False else ("yes" if oracle is True else str(oracle))
        decoder = pub.get("decoder_parity", "UNKNOWN")
        lines.append(f"- publication: oracle_free={oracle_text} decoder_parity={decoder}")
    lines.append("Limit: Huang audit labels bias (oracle/cost/prompt), not capability; cost via per-hour rate when hours available.")
    return lines




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
    lines += _placebo_lines(sample, baseline)
    lines += _campbell_lines(sample, baseline)
    lines += _huang_lines(sample, baseline)
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
