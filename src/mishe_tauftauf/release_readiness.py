"""Conservative release readiness from local CI and linked-site evidence.

Kubernetes Deployment transfer (84435cb2): declarative desired-state
reconciliation with RollingUpdate bounds (maxUnavailable/maxSurge),
readiness gating (minReadySeconds) and deadline guard
(progressDeadlineSeconds) surfaced as Available/Progressing/ReplicaFailure
conditions. Mapping: candidate SHA = desired spec, linked sites /
observed SHAs = replicas, maxUnavailable = tolerable lag, maxSurge =
extra activation budget, minReadySeconds = stability before counting as
Available, progressDeadlineSeconds = stall bound -> ProgressDeadlineExceeded.
No HOLD gate is widened here; conditions/rollout are informational.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEPLOYMENT_CONDITIONS = ("Available", "Progressing", "ReplicaFailure")
DEPLOYMENT_STRATEGY = "RollingUpdate"
ROLLOUT_MAX_UNAVAILABLE = 1
ROLLOUT_MAX_SURGE = 1
ROLLOUT_PROGRESS_DEADLINE_SECONDS = 600
ROLLOUT_MIN_READY_SECONDS = 30


def rollout_has_stalled(*, elapsed_seconds, deadline_seconds=ROLLOUT_PROGRESS_DEADLINE_SECONDS) -> bool:
    try:
        if elapsed_seconds is None:
            return False
        return float(elapsed_seconds) > float(deadline_seconds)
    except (TypeError, ValueError):
        return False


def is_available_with_min_ready(*, observed_shas, candidate_sha, ready_for_seconds, min_ready_seconds=ROLLOUT_MIN_READY_SECONDS) -> bool:
    try:
        if not isinstance(candidate_sha, str) or len(candidate_sha) != 40:
            return False
        if not isinstance(observed_shas, list) or not observed_shas:
            return False
        if not all(isinstance(s, str) and s == candidate_sha for s in observed_shas):
            return False
        if ready_for_seconds is None:
            return False
        return float(ready_for_seconds) >= float(min_ready_seconds)
    except (TypeError, ValueError):
        return False


def deployment_conditions_for(
    *,
    candidate_sha,
    ci_state,
    ci_sha,
    ci_age_seconds,
    linked_sites,
    observed_shas=None,
    observed_ready_seconds=None,
    elapsed_seconds=None,
) -> dict[str, object]:
    ready_val = observed_ready_seconds
    if isinstance(ready_val, (list, tuple)):
        vals = [v for v in ready_val if isinstance(v, (int, float))]
        ready_val = min(vals) if vals else None
    elif isinstance(ready_val, dict):
        vals = [v for v in ready_val.values() if isinstance(v, (int, float))]
        ready_val = min(vals) if vals else None
    try:
        ci_ok = ci_state == "pass" and ci_sha == candidate_sha and isinstance(ci_age_seconds, (int, float)) and 0 <= float(ci_age_seconds) <= 300
    except (TypeError, ValueError):
        ci_ok = False
    candidate_ok = isinstance(candidate_sha, str) and len(candidate_sha) == 40
    has_replica_failure = False
    if isinstance(linked_sites, list):
        for site in linked_sites:
            if not isinstance(site, dict) or site.get("error") or not site.get("sha"):
                has_replica_failure = True
                break
    elif linked_sites is not None:
        has_replica_failure = True
    if observed_shas is None:
        return {
            "available": "unknown",
            "progressing": "unknown",
            "failed": bool(has_replica_failure),
            "reason": "ReplicaFailure" if has_replica_failure else "observation unavailable",
        }
    if isinstance(observed_shas, list):
        available_replicas = sum(1 for s in observed_shas if s == candidate_sha)
        unavailable_replicas = len(observed_shas) - available_replicas
    else:
        available_replicas = 0
        unavailable_replicas = 0
    is_avail = False
    if candidate_ok:
        if isinstance(ready_val, (int, float)):
            is_avail = is_available_with_min_ready(
                observed_shas=observed_shas,
                candidate_sha=candidate_sha,
                ready_for_seconds=ready_val,
                min_ready_seconds=ROLLOUT_MIN_READY_SECONDS,
            )
        else:
            is_avail = isinstance(observed_shas, list) and bool(observed_shas) and all(s == candidate_sha for s in observed_shas)
    if candidate_ok and ci_ok and is_avail and not has_replica_failure:
        return {
            "available": True,
            "progressing": False,
            "failed": False,
            "reason": "MinimumReplicasAvailable",
            "availableReplicas": available_replicas,
            "unavailableReplicas": unavailable_replicas,
            "desired": len(observed_shas) if isinstance(observed_shas, list) else 0,
        }
    try:
        stalled = rollout_has_stalled(elapsed_seconds=elapsed_seconds) if elapsed_seconds is not None else False
    except (TypeError, ValueError):
        stalled = False
    if stalled:
        return {
            "available": False,
            "progressing": False,
            "failed": True,
            "reason": "ProgressDeadlineExceeded",
            "availableReplicas": available_replicas,
            "unavailableReplicas": unavailable_replicas,
            "desired": len(observed_shas) if isinstance(observed_shas, list) else 0,
        }
    if has_replica_failure:
        return {
            "available": False,
            "progressing": False,
            "failed": True,
            "reason": "ReplicaFailure",
            "availableReplicas": available_replicas,
            "unavailableReplicas": unavailable_replicas,
            "desired": len(observed_shas) if isinstance(observed_shas, list) else 0,
        }
    if candidate_ok:
        return {
            "available": False,
            "progressing": True,
            "failed": False,
            "reason": "ReplicaSetUpdated",
            "availableReplicas": available_replicas,
            "unavailableReplicas": unavailable_replicas,
            "desired": len(observed_shas) if isinstance(observed_shas, list) else 0,
        }
    return {
        "available": False,
        "progressing": False,
        "failed": False,
        "reason": "candidate SHA not available",
        "availableReplicas": available_replicas,
        "unavailableReplicas": unavailable_replicas,
        "desired": 0,
    }


def report(home: Path) -> dict[str, object]:
    workspace = home.parent
    reasons: list[str] = []
    try:
        from .runtime_source import source_for
        workspace = source_for(home, workspace)
        candidate = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(workspace), "status", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        if dirty:
            reasons.append("core worktree has unlanded changes")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        candidate = ""
        reasons.append(f"candidate SHA unavailable: {exc}")
    if len(candidate) != 40:
        reasons.append("candidate SHA is not a full Git object ID")

    ci: dict = {}
    ci_age: float | None = None
    elapsed: float | None = None
    try:
        ci = json.loads((home / "ci" / "latest.json").read_text(encoding="utf-8"))
        if not isinstance(ci, dict):
            raise ValueError("CI must be an object")
    except (OSError, ValueError):
        ci = {}
        reasons.append("CI evidence unavailable")
    if ci.get("state") != "pass" or ci.get("sha") != candidate:
        reasons.append("CI is not PASS for the exact candidate SHA")
    try:
        checked = datetime.fromisoformat(ci["checked"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        ci_age = age
        elapsed = age
        if not 0 <= age <= 300:
            raise ValueError("stale")
    except (KeyError, ValueError, TypeError, AttributeError):
        reasons.append("CI evidence is stale or has no valid timestamp")

    registry: dict = {}
    try:
        registry = json.loads((home / "health" / "linked-sites.json").read_text(encoding="utf-8"))
        if not isinstance(registry, dict):
            raise ValueError("registry must be an object")
    except (OSError, ValueError):
        registry = {}
        reasons.append("linked-site registry unavailable")
    sites = registry.get("sites") if registry.get("version") == 1 else None
    if not isinstance(sites, list):
        reasons.append("linked-site registry version/schema unsupported")
    else:
        for index, site in enumerate(sites):
            if not isinstance(site, dict) or site.get("error") or not site.get("sha"):
                reasons.append(f"linked site {index} has an error or missing applied SHA")
            elif site.get("sha") != candidate:
                reasons.append(f"linked site {index} SHA does not match candidate")

    state = "READY" if not reasons else "HOLD"
    result: dict[str, object] = {"state": state, "candidate_sha": candidate or "unknown", "reasons": reasons}
    try:
        observed_shas: list[str] | None = None
        observed_ready_seconds: object = None
        for snap in (home / "health" / "observed-shas.json", home / "health" / "observed.json"):
            if snap.exists():
                try:
                    data = json.loads(snap.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        observed_shas = [str(x) for x in data]
                    elif isinstance(data, dict) and isinstance(data.get("shas"), list):
                        observed_shas = [str(x) for x in data["shas"]]
                        if "ready_for_seconds" in data:
                            observed_ready_seconds = data["ready_for_seconds"]
                    break
                except (OSError, ValueError):
                    pass
        if observed_shas is None and isinstance(sites, list):
            observed_shas = [str(s.get("sha") or "") for s in sites if isinstance(s, dict)]
            if not observed_shas:
                observed_shas = None
        paused = False
        try:
            p = home / "health" / "rollout-paused.json"
            if p.exists():
                pj = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(pj, dict) and pj.get("paused") is True:
                    paused = True
                elif pj is True:
                    paused = True
        except (OSError, ValueError):
            paused = False
        cond = deployment_conditions_for(
            candidate_sha=candidate if len(candidate) == 40 else "",
            ci_state=ci.get("state"),
            ci_sha=ci.get("sha"),
            ci_age_seconds=ci_age,
            linked_sites=sites if isinstance(sites, list) else sites,
            observed_shas=observed_shas,
            observed_ready_seconds=observed_ready_seconds if observed_ready_seconds is not None else ROLLOUT_MIN_READY_SECONDS,
            elapsed_seconds=elapsed,
        )
        if paused:
            cond = dict(cond)
            cond["progressing"] = False
            cond["reason"] = "Paused"
        conditions = {
            "Available": cond.get("available"),
            "Progressing": cond.get("progressing"),
            "ReplicaFailure": bool(cond.get("failed") and cond.get("reason") == "ReplicaFailure"),
        }
        if cond.get("reason") == "ProgressDeadlineExceeded":
            conditions["ReplicaFailure"] = False
        result["conditions"] = conditions
        result["conditions_raw"] = cond
        if isinstance(observed_shas, list):
            desired = len(observed_shas) if observed_shas else (len(sites) if isinstance(sites, list) else 0)
            available_replicas = sum(1 for s in observed_shas if s == candidate) if len(candidate) == 40 else 0
            unavailable_replicas = desired - available_replicas if isinstance(desired, int) else 0
            rollout: dict[str, object] = {
                "desired": desired,
                "availableReplicas": available_replicas,
                "unavailableReplicas": unavailable_replicas,
                "strategy": DEPLOYMENT_STRATEGY,
                "maxUnavailable": ROLLOUT_MAX_UNAVAILABLE,
                "maxSurge": ROLLOUT_MAX_SURGE,
                "progressDeadlineSeconds": ROLLOUT_PROGRESS_DEADLINE_SECONDS,
                "minReadySeconds": ROLLOUT_MIN_READY_SECONDS,
            }
            if elapsed is not None:
                rollout["elapsedSeconds"] = elapsed
            if paused:
                rollout["paused"] = True
            result["rollout"] = rollout
        else:
            result["rollout"] = {
                "desired": "unknown",
                "availableReplicas": "unknown",
                "unavailableReplicas": "unknown",
                "strategy": DEPLOYMENT_STRATEGY,
                "maxUnavailable": ROLLOUT_MAX_UNAVAILABLE,
                "maxSurge": ROLLOUT_MAX_SURGE,
                "progressDeadlineSeconds": ROLLOUT_PROGRESS_DEADLINE_SECONDS,
                "minReadySeconds": ROLLOUT_MIN_READY_SECONDS,
            }
            if elapsed is not None:
                result["rollout"]["elapsedSeconds"] = elapsed
    except Exception:
        pass
    return result


def line(home: Path) -> str:
    try:
        r = report(home)
    except Exception as exc:
        return f"release UNKNOWN: {exc}"
    state = r.get("state", "UNKNOWN")
    sha = str(r.get("candidate_sha", "unknown"))
    short = sha[:12] if len(sha) >= 12 else sha
    conditions = r.get("conditions") if isinstance(r.get("conditions"), dict) else {}
    rollout = r.get("rollout") if isinstance(r.get("rollout"), dict) else {}
    available = conditions.get("Available", "unknown") if conditions else "unknown"
    progressing = conditions.get("Progressing", "unknown") if conditions else "unknown"
    raw_reason = ""
    if isinstance(r.get("conditions_raw"), dict):
        raw_reason = str(r["conditions_raw"].get("reason", ""))
    desired = rollout.get("desired", "unknown")
    avail_n = rollout.get("availableReplicas", "unknown")
    unavail_n = rollout.get("unavailableReplicas", "unknown")
    base = f"release {state} {short} deploy={DEPLOYMENT_STRATEGY.lower()} available={str(available).lower()} progressing={str(progressing).lower()}"
    detail = f" desired={desired} availableReplicas={avail_n} unavailableReplicas={unavail_n} strategy={DEPLOYMENT_STRATEGY} maxUnavailable={ROLLOUT_MAX_UNAVAILABLE} maxSurge={ROLLOUT_MAX_SURGE}"
    if raw_reason:
        detail += f" reason={raw_reason}"
    reasons = r.get("reasons")
    if isinstance(reasons, list) and reasons:
        detail += f" reasons={'; '.join(str(x) for x in reasons[:2])}"
    return base + detail
