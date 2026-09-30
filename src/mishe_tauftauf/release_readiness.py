"""Conservative release readiness from local CI and linked-site evidence."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def report(home: Path) -> dict[str, object]:
    """Report deployment convergence; this is not permission to review or land code."""
    workspace = home.parent
    reasons: list[str] = []
    try:
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
    except (OSError, subprocess.SubprocessError) as exc:
        candidate = ""
        reasons.append(f"candidate SHA unavailable: {exc}")
    if len(candidate) != 40:
        reasons.append("candidate SHA is not a full Git object ID")

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
        if not 0 <= age <= 300:
            raise ValueError("stale")
    except (KeyError, ValueError, TypeError, AttributeError):
        reasons.append("CI evidence is stale or has no valid timestamp")

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
    return {"state": "READY" if not reasons else "HOLD", "candidate_sha": candidate or "unknown",
            "reasons": reasons}
