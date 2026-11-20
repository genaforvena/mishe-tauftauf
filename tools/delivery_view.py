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

Between delivered promotions it measures the previous finish to the next start.
The interval can include time before a commit and time spent on unsuccessful attempts.

A rolled-back run shows the failure reason and the recorded rollback duration if available.
A failed recovery stays UNKNOWN and identifies the stage when the record carries that evidence.
Missing or unreadable evidence prints as UNKNOWN and never becomes a zero. This is a report,
not a detector: it always exits 0 unless it cannot run.
"""
from __future__ import annotations

import argparse
import importlib.util
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

FENCE_QUIET_S = 1.0
"""A fence under one second is a quiet-gap acquisition, not a wake wait.

``held_delivery_lock`` retries the dispatch lock on a 1 s sleep while no hold is written
(``release_promotion.py``), so a promotion that found a gap on its first try crosses
``waiting_since`` to ``activation_started_at`` in only the two record saves (~20 ms measured).
An exact-zero test never fires and hides that the quiet-gap path works; one second separates
a first-try acquisition from even a single retry.
"""

FENCE_HOLD_S = 600.0
"""Legacy patience for promotions written before ``quiet_poll_s`` (release_promotion.py).

Promotions now record the patience they actually used (120 s since 2026-11-20). Records with no
such field keep the old 600 s rule, so a regime change does not silently reclassify history.
"""


def quiet_poll_s(record: dict) -> float:
    """The quiet-poll patience in force when this promotion waited for the dispatch lock."""
    value = record.get("quiet_poll_s")
    return value if type(value) in (int, float) and value > 0 else FENCE_HOLD_S

RECENT_N = 10
"""How many newest delivered promotions get a stage mean beside the window's mean.

A 24 h window mixes regimes: on 2026-11-20 the check stage fell from about 16 min to
about 8.5 min when the suite began running with two xdist workers, while the window
mean stayed near 14 min. A reader asking where delivery time goes now must see both,
or the lagging window figure sends the next investigation at an already-fixed stage.
"""


def recent_stages(delivered: list[dict], count: int = RECENT_N) -> str:
    """Stage means of the newest deliveries, each beside the whole window's mean."""
    newest = sorted(delivered, key=lambda r: r["created_at"])[-count:]
    if not newest:
        return "newest delivered promotions: UNKNOWN (none in the window)"
    recent, whole = summarize(newest), summarize(delivered)
    parts = []
    for key, label in (("check_s", "check"), ("wake_wait_s", "fence"),
                       ("checked_to_live_s", "total")):
        row, window_row = recent.get(key, {}), whole.get(key, {})
        value = fmt_s(row["mean"]) if row else "UNKNOWN"
        before = f" (window {fmt_s(window_row['mean'])})" if window_row else ""
        parts.append(f"{label} {value}{before}")
    return (f"newest {len(newest)} delivered (since {fmt_ts(newest[0]['created_at'])}): "
            + "  ".join(parts))



def _wake_boundary():
    """Load the ledger decoder beside this script, by path: unit tests import us by path too."""
    path = Path(__file__).resolve().with_name("wake_view.py")
    spec = importlib.util.spec_from_file_location("wake_view_boundary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    ``check.at`` is the stage's end; the check stage subsumes any queue wait before the run
    started; that split is not recorded.
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


def fence_modes(delivered: list[dict]) -> dict[str, list[float]]:
    """Fence seconds split by how the dispatch lock was acquired, from record timestamps alone.

    ``quiet`` (<1 s): the first poll found a gap. ``polled``: a gap appeared within that
    promotion's quiet poll. ``hold``: no gap appeared, the hold was written, and the promotion
    waited for running wakes to release the shared lock. The patience comes from each record
    (``quiet_poll_s``; 600 s for legacy records), not one constant across regimes. Unmeasured
    runs are absent, never zero.
    """
    out: dict[str, list[float]] = {"quiet": [], "polled": [], "hold": []}
    for record in delivered:
        seconds = stages(record)["wake_wait_s"]
        if seconds is None:
            continue
        if seconds < FENCE_QUIET_S:
            out["quiet"].append(seconds)
        elif seconds < quiet_poll_s(record):
            out["polled"].append(seconds)
        else:
            out["hold"].append(seconds)
    return out


def poll_wake_starts(delivered: list[dict], starts: list[float],
                     windows: list[tuple[float, float, float]] | None = None) -> dict | None:
    """Wake starts inside each hold-wait fence's quiet poll versus after the hold.

    Only fences at or beyond their own patience are counted: a quiet or polled fence never wrote
    a hold, so a start inside it is not a poll-start. ``starts`` are wake start epochs from the
    routing ledger. A start during the poll grabs the shared dispatch lock and can extend the
    fence; the hold blocks new starts only after that promotion's poll expires. ``windows``, when
    given, restricts the count to hold-wait fences with a recorded start or positive overlap.
    Returns None when no such fence is measured.
    """
    if windows is None:
        holds = []
        for record in delivered:
            seconds = stages(record)["wake_wait_s"]
            if seconds is None:
                continue
            patience = quiet_poll_s(record)
            if seconds < patience:
                continue
            ws, ac = record.get("waiting_since"), record.get("activation_started_at")
            if isinstance(ws, (int, float)) and isinstance(ac, (int, float)):
                holds.append((ws, ac, patience))
    else:
        holds = list(windows)
    if not holds:
        return None
    poll = [sum(1 for s in starts if ws <= s < ws + patience) for ws, _, patience in holds]
    after = [sum(1 for s in starts if ws + patience <= s < ac) for ws, ac, patience in holds]
    return {
        "holds": len(holds),
        "poll_mean": sum(poll) / len(poll),
        "poll_max": max(poll),
        "after_mean": sum(after) / len(after),
        "with_poll_start": sum(1 for n in poll if n >= 1),
    }


def fence_holders(events: list[dict] | None, delivered: list[dict]) -> dict[str, dict[str, float]] | None:
    """Attribute hold-wait fence coverage to the wakes that supplied it, by candidate and role.

    Only a wake with a paired select/outcome span counts: a bare select is a start, not evidence
    that the wake held the lock for any interval. Each overlap is clipped to one fence, so a
    wake that ran before or after a fence does not donate those seconds. An uncovered fence
    contributes nothing and never becomes a fabricated zero. Returns None when no hold-wait
    fence has a measured holder, so a broken ledger stays UNKNOWN instead of reading as an
    empty distribution.
    """
    if not events:
        return None
    first: dict[str, tuple[str, str, float]] = {}
    ended: dict[str, float] = {}
    for ev in events:
        wake, at = ev.get("wake"), ev.get("at")
        if not wake or type(at) not in (int, float):
            continue
        if ev.get("event") == "select":
            meta = (str(ev.get("role") or "?"), str(ev.get("key") or "?"), at)
            if wake not in first or at < first[wake][2]:
                first[wake] = meta
        elif ev.get("event") == "outcome" and (wake not in ended or at > ended[wake]):
            ended[wake] = at
    spans = [(role, key, at, ended[wake], wake) for wake, (role, key, at) in first.items()
             if wake in ended and ended[wake] >= at]
    if not spans:
        return None
    by_candidate: dict[str, float] = {}
    by_role: dict[str, float] = {}
    distinct: list[int] = []
    for rec in delivered:
        fence = stages(rec)["wake_wait_s"]
        if fence is None or fence < quiet_poll_s(rec):
            continue
        ws, ac = rec.get("waiting_since"), rec.get("activation_started_at")
        if not (isinstance(ws, (int, float)) and isinstance(ac, (int, float)) and ac > ws):
            continue
        holders: set[str] = set()
        for role, key, a, b, wake in spans:
            overlap = min(ac, b) - max(ws, a)
            if overlap <= 0:
                continue
            holders.add(wake)
            by_candidate[key] = by_candidate.get(key, 0.0) + overlap
            by_role[role] = by_role.get(role, 0.0) + overlap
        if holders:
            distinct.append(len(holders))
    if not by_candidate:
        return None
    return {"by_candidate": by_candidate, "by_role": by_role, "distinct": distinct}


def _holder_line(label: str, values: dict[str, float], limit: int = 4) -> str:
    """One bounded, descending line of holder seconds with each holder's share."""
    total = sum(values.values())
    top = sorted(values.items(), key=lambda item: (-item[1], item[0]))[:limit]
    parts = [f"{name} {int(seconds)}s ({100 * seconds / total:.0f}%)" for name, seconds in top]
    if len(values) > limit:
        parts.append(f"+{len(values) - limit} more")
    return f"  fence-holder wake-seconds by {label}: " + ", ".join(parts)



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


def promotion_gaps(records: list[dict]) -> list[float]:
    """Seconds from one delivered promotion's finish to the next promotion's start.

    Pair delivered records in start order. Any intervening attempts fall inside the
    interval. A torn finish timestamp contributes no sample.
    """
    delivered = sorted((r for r in records if r.get("phase") == "delivered"),
                       key=lambda r: r["created_at"])
    gaps = []
    for before, after in zip(delivered, delivered[1:]):
        end = before.get("delivered_at")
        if not isinstance(end, (int, float)) or isinstance(end, bool):
            continue
        gap = after["created_at"] - end
        if gap >= 0:
            gaps.append(gap)
    return gaps


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
    """Format a record timestamp, with UNKNOWN for invalid or unrepresentable epochs."""
    if type(epoch) not in (int, float) or not 0 < epoch < float("inf"):
        return "UNKNOWN"
    try:
        utc = time.gmtime(epoch)
    except (OverflowError, OSError):
        return "UNKNOWN"
    return time.strftime("%m-%d %H:%M:%SZ", utc)


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
    busy = [r for r in window if r.get("phase") == "delivery-busy"]
    lines.append(f"promotions in window: {len(window)} delivered {len(delivered)} "
                 f"rolled-back/UNKNOWN {len(recoveries)} delivery-busy {len(busy)}")
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
    lines.append("  " + recent_stages(delivered))
    gaps = promotion_gaps(delivered)
    gap_label = "delivered finish -> next delivered start"
    if gaps:
        ordered = sorted(gaps)
        p90 = ordered[min(len(ordered) - 1, max(0, round(0.9 * len(ordered)) - 1))]
        lines.append(f"  {gap_label} mean {fmt_s(sum(gaps) / len(gaps)):>9s}"
                     f"  p90 {fmt_s(p90):>9s}  max {fmt_s(ordered[-1]):>9s}  (n={len(gaps)})")
    else:
        lines.append(f"  {gap_label}  UNKNOWN (no paired delivered records)")
    modes = fence_modes(delivered)
    measured = sum(len(v) for v in modes.values())
    if measured:
        quiet, polled, hold = modes["quiet"], modes["polled"], modes["hold"]
        polled_mean = f" mean {fmt_s(sum(polled) / len(polled))}" if polled else ""
        hold_stats = (f" mean {fmt_s(sum(hold) / len(hold))} max {fmt_s(max(hold))}"
                      if hold else "")
        patiences = sorted({quiet_poll_s(r) for r in delivered
                            if stages(r)["wake_wait_s"] is not None})
        patience_text = (f"{patiences[0]:g}s" if len(patiences) == 1
                         else f"{patiences[0]:g}-{patiences[-1]:g}s")
        lines.append(f"  fence modes: {len(quiet)}/{measured} quiet gaps (<1s),"
                     f" {len(polled)} polled (gap inside quiet poll{polled_mean}),"
                     f" {len(hold)} hold-waits (hold written{hold_stats})")
        lines.append(f"  quiet poll patience {patience_text} from promotion records;"
                     " hold-waits then wait for a running wake to release the shared dispatch lock")
        boundary = _wake_boundary()
        events = boundary.load_events(home)
        if events is None:
            lines.append("  hold-wait/wake overlap UNKNOWN (routing ledger unreadable)")
        else:
            intervals = boundary.wake_intervals(events)
            starts = list(boundary.first_wake_starts(events).values())
            # A fence with no interval inside it cannot show zero wake overlap: the ledger may
            # simply have stopped recording before that fence. Require positive per-fence
            # evidence for coverage, and expose fences we cannot measure instead of folding
            # them in as zero. A recorded select establishes a start even without an outcome.
            hold_windows: list[tuple[float, float]] = []
            ledger_evidenced: list[tuple[float, float, float]] = []
            overlap: list[float] = []
            uncovered = torn = 0
            for rec in delivered:
                fence = stages(rec)["wake_wait_s"]
                if fence is None:
                    continue
                patience = quiet_poll_s(rec)
                if fence < patience:
                    continue
                ws, ac = rec.get("waiting_since"), rec.get("activation_started_at")
                if not (isinstance(ws, (int, float)) and isinstance(ac, (int, float))):
                    continue
                hold_windows.append((ws, ac))
                value = boundary.interval_coverage(intervals, ws, ac)
                has_overlap = value is not None and value > 0
                if value is None:
                    torn += 1
                elif has_overlap:
                    overlap.append(value)
                else:
                    uncovered += 1
                if has_overlap or any(ws <= start < ac for start in starts):
                    ledger_evidenced.append((ws, ac, patience))
            unevidenced = uncovered + torn
            if not hold_windows:
                lines.append("  hold-wait/wake overlap UNKNOWN (no paired wake intervals)")
            elif not overlap:
                lines.append("  hold-wait/wake overlap UNKNOWN (no wake interval covers a measured fence)")
                lines.append(f"  {unevidenced} of {len(hold_windows)} hold-wait fences had no ledger"
                             f" interval inside ({torn} torn); excluded, not read as zero")
            else:
                count = f"{len(overlap)}/{len(hold_windows)}" if unevidenced else str(len(overlap))
                lines.append(f"  hold-wait/wake overlap: {count} hold-waits,"
                             f" mean {100 * sum(overlap) / len(overlap):.1f}% of each fence"
                             " covered by a running wake")
                if unevidenced:
                    lines.append(f"  {unevidenced} hold-wait fence(s) UNKNOWN: no wake interval inside"
                                 f" ({torn} torn), excluded from the overlap mean")
            no_ledger = len(hold_windows) - len(ledger_evidenced)
            poll = poll_wake_starts(delivered, starts, ledger_evidenced)
            if poll is not None:
                suffix = (f"; {no_ledger} fence(s) UNKNOWN (no ledger interval or start inside), excluded"
                          if no_ledger else "")
                lines.append(f"  recorded wake starts in the quiet poll ({patience_text}):"
                             f" mean {poll['poll_mean']:.2f}/hold-wait (max {poll['poll_max']}),"
                             f" {poll['with_poll_start']}/{poll['holds']} fences had >=1;"
                             f" after hold {poll['after_mean']:.2f}{suffix}")
            elif hold_windows:
                lines.append("  wake starts in the quiet poll UNKNOWN (same missing ledger evidence)")
            holders = fence_holders(events, delivered)
            if holders is None:
                lines.append("  fence-holder attribution UNKNOWN (no paired wake span covers a hold-wait)")
            else:
                lines.append(_holder_line("candidate", holders["by_candidate"]))
                lines.append(_holder_line("role", holders["by_role"]))
                d = holders["distinct"]
                lines.append(f"  distinct wakes per hold-wait fence: mean {sum(d) / len(d):.1f}"
                             f" (max {max(d)}), {len(d)} fences")
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
    if busy:
        newest_busy = max(busy, key=lambda r: r["created_at"])
        lines.append(f"  no-effect delivery-busy refusal (newest): {str(newest_busy.get('sha', '?'))[:8]}"
                     f" launched {fmt_ts(newest_busy.get('created_at'))}"
                     " — admission was held, no pin byte switched")
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
