#!/usr/bin/env python3
"""Checked-to-live report: how long a checked change waits before it is live on the pin.

Reads the single promote path's records (SITE/work/releases/*.json, read-only) plus local git.
For each delivered promotion in the window it measures the stages separately where the record
permits:

  commit -> created_at              a checked change waits before a promotion starts
  created_at -> check.at            the whole-suite check stage (no intra-check timestamps recorded)
  waiting_since -> activation_started_at   the pin fence holding activation until wakes end
  activation_started_at -> delivered_at     consumer drain + functional observation
  delivered_at -> reviewed_at       independent review after the pin moved

A rolled-back run shows the failure reason and the recorded rollback duration if available.
A failed recovery stays UNKNOWN and identifies the stage when the record carries that evidence.
Missing or unreadable evidence prints as UNKNOWN and never becomes a zero. This is a report,
not a detector: it always exits 0 unless it cannot run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_HOME = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME")
                   or ".mishe-tauftauf")
DEFAULT_WINDOW_S = 24 * 3600.0
LIVE_MAX_AGE_S = 3 * 3600.0
"""An unfinished record older than this is not trusted as live: a promote that died without
finishing leaves its phase behind (2026-11-18 06:13Z dispatch race left 0148ab3e reading
``waiting-for-wakes`` for good). Measured checked-to-live max is under 1 h, so 3 h bounds any
legitimate run; pin-lag (checks/pin-lag.py) already uses the same red-age bound."""


def git(repo: Path, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    """Run git read-only; ``(1, "")`` on any failure so a caller can print UNKNOWN, not crash."""
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              timeout=timeout, env={"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin",
                                                    "HOME": str(Path.home())})
        return done.returncode, done.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def load_records(home: Path) -> list[dict]:
    records = []
    for path in sorted((home / "work" / "releases").glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue  # a torn record is unreadable evidence, not an empty series
    return [r for r in records if isinstance(r.get("created_at"), (int, float))]


def stages(record: dict) -> dict:
    """Per-stage seconds of one promotion, ``None`` where the record does not permit the measure.

    ``check.at`` is the stage's end (delivery_check.py runs about 16 min inside it), so the check
    stage subsumes any queue wait before the run started; that split is not recorded.
    """
    def span(start: float | None, end: float | None) -> float | None:
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        return end - start

    return {
        "check_s": span(record.get("created_at"), _check_at(record)),
        "wake_wait_s": span(record.get("waiting_since"), record.get("activation_started_at")),
        "drain_observe_s": span(record.get("activation_started_at"), record.get("delivered_at")),
        "review_s": span(record.get("delivered_at"), record.get("reviewed_at")),
        "checked_to_live_s": span(record.get("created_at"), record.get("delivered_at")),
    }


def _check_at(record: dict) -> float | None:
    check = record.get("check")
    at = check.get("at") if isinstance(check, dict) else None
    return at if isinstance(at, (int, float)) else None


def summarize(rows: list[dict]) -> dict[str, dict[str, float]]:
    """Mean / p90 (nearest-rank) / max per stage over the window's rows; a stage with no sample is {}."""
    out: dict[str, dict[str, float]] = {}
    for key in ("check_s", "wake_wait_s", "drain_observe_s", "review_s", "checked_to_live_s"):
        values = sorted(v for v in (stages(r).get(key) for r in rows) if v is not None)
        if not values:
            out[key] = {}
            continue
        p90 = values[min(len(values) - 1, max(0, round(0.9 * len(values)) - 1))]
        out[key] = {"mean": sum(values) / len(values), "p90": p90, "max": values[-1], "n": float(len(values))}
    return out


def commit_log(repo: Path, revision_range: str) -> list[tuple[str, float, str]]:
    """(sha, commit_time, subject) of the range's commits, oldest first; empty when git cannot read."""
    code, out = git(repo, "log", "--reverse", "--format=%H;%ct;%s", revision_range)
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(";", 2)
        if len(parts) == 3:
            try:
                rows.append((parts[0], float(parts[1]), parts[2]))
            except ValueError:
                continue
    return rows


def commit_waits(records: list[dict], repo: Path) -> list[float]:
    """Seconds each delivered promotion's commits waited from commit time to promotion start."""
    waits = []
    for record in records:
        previous = record.get("previous")
        if not previous or record.get("phase") != "delivered":
            continue
        for _sha, committed, _subject in commit_log(repo, f"{previous}..{record.get('sha')}"):
            waits.append(record["created_at"] - committed)
    return [w for w in waits if w >= 0]


def pin_sha(home: Path) -> str | None:
    try:
        target = Path(os.readlink(home / "runtime" / "current"))
        return target.name
    except OSError:
        return None


def fmt_s(seconds: float | None) -> str:
    if seconds is None:
        return "UNKNOWN"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def fmt_ts(epoch: float | None) -> str:
    return time.strftime("%m-%d %H:%M:%SZ", time.gmtime(epoch)) if epoch else "UNKNOWN"


def render(home: Path, repo: Path, window_s: float, now: float) -> str:
    records = load_records(home)
    lines = [f"Checked-to-live report  {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(now))}",
             f"window: last {int(window_s // 3600)} h of promotions; pin: {pin_sha(home) or 'UNKNOWN'}"]
    if not records:
        lines.append("UNKNOWN: no readable promotion records under work/releases")
        return "\n".join(lines)
    window = [r for r in records if now - r["created_at"] <= window_s]
    delivered = [r for r in window if r.get("phase") == "delivered"]
    recoveries = [r for r in window if r.get("phase") in ("UNKNOWN", "rolled-back")]
    lines.append(f"promotions in window: {len(window)} delivered {len(delivered)} "
                 f"rolled-back/UNKNOWN {len(recoveries)}")
    s = summarize(delivered)
    for key, label in (("commit_wait_s", "commit -> promotion start"),
                       ("check_s", "check stage (created -> check done)"),
                       ("wake_wait_s", "waiting for wakes (fence)"),
                       ("drain_observe_s", "drain + functional observation"),
                       ("review_s", "review after delivery"),
                       ("checked_to_live_s", "checked-to-live total (created -> delivered)")):
        if key == "commit_wait_s":
            waits = commit_waits(delivered, repo)
            row = {"mean": sum(waits) / len(waits), "p90": sorted(waits)[max(0, round(0.9 * len(waits)) - 1)],
                   "max": max(waits), "n": float(len(waits))} if waits else {}
        else:
            row = s.get(key, {})
        if row:
            lines.append(f"  {label:36s} mean {fmt_s(row['mean']):>9s}  p90 {fmt_s(row['p90']):>9s}"
                         f"  max {fmt_s(row['max']):>9s}  (n={int(row['n'])})")
        else:
            lines.append(f"  {label:36s} UNKNOWN (no record in the window carries both timestamps)")
    zero_fence = sum(1 for r in delivered if stages(r)["wake_wait_s"] == 0)
    if zero_fence:
        lines.append(f"  fence held nothing in {zero_fence}/{len(delivered)} delivered runs"
                     f" (no wake was live at activation)")
    rollback_done, rollback_probe_failed = [], []
    for record in recoveries:
        sha = record.get("sha", "?")[:8]
        completed = record.get("phase") == "rolled-back"
        revert_started = record.get("revert_started_at")
        if not completed and type(revert_started) in (int, float) and 0 < revert_started < float("inf"):
            detail = str(record.get("revert_error") or "")[:160]
            line = f"  UNKNOWN (explicit revert failed) {sha} after {fmt_ts(revert_started)}"
            lines.append(f"{line} — {detail}" if detail else line)
            continue

        probe = record.get("rollback_probe")
        probe_code = probe.get("code") if isinstance(probe, dict) else None
        detail = record.get("failure") or "UNKNOWN"
        samples = None
        if completed:
            label, outcome = "rolled back", "completed"
            samples = rollback_done
        elif record.get("rollback_error"):
            label, outcome = "UNKNOWN (rollback restore failed)", "restore failed"
            detail = record["rollback_error"]
        elif type(probe_code) is int and probe_code != 0:
            label, outcome = "UNKNOWN (rollback probe failed)", "probe failed"
            samples = rollback_probe_failed
        else:
            label, outcome = "UNKNOWN (recovery stage unrecorded)", "failed"
        activated = fmt_ts(record.get("activation_started_at"))
        lines.append(f"  {label} {sha} after {activated} — {str(detail)[:160]}")
        start = record.get("rollback_at")
        end = record.get("rollback_completed_at" if completed else "rollback_failed_at")
        if type(start) in (int, float) and type(end) in (int, float) and 0 < start <= end < float("inf"):
            elapsed = end - start
            lines.append(f"    rollback {fmt_s(elapsed)} ({outcome})")
            if samples is not None:
                samples.append(elapsed)
        else:
            lines.append("    rollback duration UNKNOWN (missing or invalid timestamps)")
    for label, times in (("completed rollback", rollback_done),
                         ("failed-probe rollback", rollback_probe_failed)):
        if times:
            lines.append(f"  {label} time mean {fmt_s(sum(times) / len(times))}"
                         f" max {fmt_s(max(times))}  (n={len(times)})")
    pin = pin_sha(home)
    if pin:
        waiting = commit_log(repo, f"{pin}..HEAD")
    else:
        waiting = []
    if pin is None:
        lines.append("UNKNOWN: no pin to measure the waiting commits against")
    elif waiting:
        oldest = waiting[0]
        lines.append(f"oldest checked fix still waiting: {oldest[0][:8]} \"{oldest[2]}\" committed"
                     f" {fmt_ts(oldest[1])}, aged {fmt_s(now - oldest[1])}; {len(waiting)} commit(s) on HEAD"
                     " not on the pin")
    else:
        lines.append("no checked fix is waiting: the pin is at HEAD")
    in_flight = [r for r in records if r.get("phase") in ("checking", "waiting-for-wakes", "activating",
                                                          "review", "applying")]
    if in_flight:
        live = [r for r in in_flight if now - r["created_at"] <= LIVE_MAX_AGE_S]
        if live:
            newest = max(live, key=lambda r: r["created_at"])
            lines.append(f"in flight: {newest.get('sha', '?')[:8]} phase {newest['phase']}"
                         f" since {fmt_ts(newest.get('created_at'))} for this promotion"
                         f" (activation is held until wakes end)")
        else:
            newest = max(in_flight, key=lambda r: r["created_at"])
            lines.append("in flight: none")
            lines.append(f"  abandoned: {newest.get('sha', '?')[:8]} phase {newest['phase']}"
                         f" since {fmt_ts(newest.get('created_at'))}, older than"
                         f" {int(LIVE_MAX_AGE_S // 60)} min — a promote that died without finishing"
                         " is not trusted as live")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME, help="site home (default: env or .mishe-tauftauf)")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW_S, help="window in seconds")
    args = parser.parse_args(argv)
    print(render(args.home, args.repo, args.window, time.time()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
