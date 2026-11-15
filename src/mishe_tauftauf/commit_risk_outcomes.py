"""Ground truth for commit-risk rows: did a later fix or revert touch the same paths?

Read-only measurement. It reads the commit-risk ledger and the git history of the
checkout and reports, for every ledger row whose commit is at least ``WINDOW`` old,
whether a later commit within that window was a fix or a revert on one of the same
paths. It writes nothing and never gates delivery.

The proxy is weak on purpose: a fix-up is a sign that a commit needed follow-up work,
not proof that it was risky. A row whose commit is missing from git, or a window that
has not yet elapsed, is counted as missing or pending, never as clean. With no aged row
the state is UNKNOWN.

Parsing (ledger JSON, ``git log`` output, command-line flags) lives in
``commit_risk_text``; this module works on plain values only.
"""
from __future__ import annotations

import sys
import time

from mishe_tauftauf import commit_risk_text as text

WINDOW = 72 * 3600


def follow_ups(rows: list[dict], commits: list[dict], now: float, *, window: int = WINDOW) -> list[dict]:
    """Classify each ledger row as ``missing``, ``pending``, ``clean``, ``followed`` or ``unscored``."""
    by_sha = {c["sha"]: c for c in commits}
    fixes = [c for c in commits if text.is_fix(c["subject"])]
    results = []
    for row in rows:
        sha = row.get("sha")
        commit = by_sha.get(sha)
        if commit is None:
            results.append({"sha": sha, "status": "missing", "fix": None})
            continue
        if not commit["paths"]:
            # a merge (no per-path diff in git log) cannot be matched to a fix on its paths
            results.append({"sha": sha, "status": "unscored", "fix": None})
            continue
        if now - commit["ct"] < window:
            results.append({"sha": sha, "status": "pending", "fix": None})
            continue
        hit = None
        for fix in fixes:
            if fix["sha"] == sha or not (commit["ct"] < fix["ct"] <= commit["ct"] + window):
                continue
            if commit["paths"] & fix["paths"]:
                hit = fix["sha"]
                break
        results.append({"sha": sha, "status": "followed" if hit else "clean", "fix": hit})
    return results


def summary(results: list[dict]) -> tuple[str, str]:
    """Return (state, one line). UNKNOWN when no row is aged or any row lacks its commit."""
    total = len(results)
    missing = sum(1 for r in results if r["status"] == "missing")
    aged = [r for r in results if r["status"] in ("clean", "followed")]
    followed = sum(1 for r in aged if r["status"] == "followed")
    unscored = sum(1 for r in results if r["status"] == "unscored")
    if missing:
        return "UNKNOWN", f"{missing} of {total} ledger rows name a commit missing from git"
    if not aged:
        return "UNKNOWN", f"0 of {total} ledger rows are {WINDOW // 3600} h old; no ground truth yet"
    note = f" ({unscored} merge rows unscored)" if unscored else ""
    return "MEASURED", f"{followed} of {len(aged)} rows aged {WINDOW // 3600} h had a later fix or revert on their paths{note}"


def main(argv=None) -> int:
    args = text.outcome_args(argv)
    now = time.time() if args.now is None else args.now
    results = follow_ups(text.load_rows(args.ledger), text.git_commits(args.repo), now)
    state, line = summary(results)
    print(f"commit-risk-outcomes: {state} {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
