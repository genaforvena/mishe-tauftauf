"""Restart-cadence reader: session-scope births over the trailing window.

Isolated producer only: pure functions over caller-supplied journal lines.
No discovery wiring, no consumer, no live activation.

Contract:
- Authoritative source: journal MESSAGE lines `Started <session>-session.scope`
  under the systemd identifier (seed.py raises the resident session with
  `systemd-run --unit <session>-session.scope`). One birth per boot is
  healthy; repeated births mean the session keeps dying and being re-raised.
- Match: Python-side prefix test on MESSAGE for this plant's exact
  `<session>-session.scope` scope unit. Foreign sessions, services, and
  non-string lines never match.
- Verdicts: `steady` (<3 births and no sub-10m gap), `churn` (>=3 births in
  the trailing window OR any inter-birth gap under 10 minutes with >=2
  births), `unknown` (session missing, empty input, malformed timestamps).
- Consumer: Health RED cause; the guard's own `server cadence` log line is
  the outside-mishe witness that survives pane outages.
- Sample: `births=N gaps=[..] under_30m=K` plus the drift clause; cadence
  shape for the resolver, not just a count.

Text handling: MESSAGE prefix equality only; timestamps as caller-supplied
epoch microseconds (int). Gaps computed in minutes. No journal parsing here:
the wiring hunk owns acquisition (cursor dedup, boot pin, time window) and
passes decoded (timestamp, message) pairs in.

Parsing-policy note: this module performs only prefix equality, integer
arithmetic, and comparisons — no flagged parsing calls — so
`tools/check_parsing.py` passes with no policy addition and the frozen
legacy budget untouched. If the wiring hunk adds real parsing sites, that
hunk's author owes the reviewed exact-path boundary entry per
docs/protocol-boundaries.md.

Limits: counts births, not causes. A birth line proves re-raise, not what
killed the prior incarnation. Correlate with OOM/journal evidence separately.
Callers supply session identity; an absent session reports unknown, never
steady.
"""
from __future__ import annotations

COUNT_DRIFT_THRESHOLD = 3
"""Births in the trailing window at or above which the session is churning.

A healthy plant raises its session exactly once per boot, so 3+ births per
hour cannot be operator cadence: even a deliberate teardown plus re-raise
plus one retry is at most 2-3 widely spaced births, and sustained re-raising
means something keeps killing the scope. Today's data: 10 births in the
window, 8 of 9 inter-birth gaps under 30m, worst gap 2.7m — count>=3 fires.
"""

TIGHT_GAP_MINUTES = 10.0
"""Inter-birth gap below which a rebirth means the prior incarnation died fast.

Any rebirth within 10 minutes means the prior incarnation died within
minutes — the crash-loop signature — even when the hourly total is only 2.
Today's worst gap (2.7m) fires independently of the count clause.
"""

STEADY = "steady"
CHURN = "churn"
UNKNOWN = "unknown"


def _is_birth(message, session: str) -> bool:
    """Whether one MESSAGE line is this session's scope birth."""
    if not isinstance(message, str):
        return False
    if not isinstance(session, str) or not session:
        return False
    prefix = "Started " + session + "-session.scope"
    return message[:len(prefix)] == prefix


def assess_restarts(entries, session: str) -> dict:
    """Assess restart cadence over caller-supplied (epoch_us, message) pairs.

    Returns a plain dict: verdict plus births count, gap list, under-30m
    count, and reason. Non-list input, missing session, or malformed entries
    report unknown, never steady.
    """
    base: dict = {"verdict": UNKNOWN, "reason": "unusable-input",
                  "births": 0, "gaps": [], "under_30m": 0}
    if not isinstance(session, str) or not session:
        base["reason"] = "session-unavailable"
        return base
    if not isinstance(entries, list):
        return base
    stamps = []
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            base["reason"] = "malformed-entries"
            return base
        epoch_us, message = entry
        if not isinstance(epoch_us, int) or isinstance(epoch_us, bool):
            base["reason"] = "malformed-entries"
            return base
        if _is_birth(message, session):
            stamps.append(epoch_us)
    stamps.sort()
    births = len(stamps)
    base["births"] = births
    if births == 0:
        base["reason"] = "no-births-in-window"
        return base
    gaps = []
    idx = 1
    while idx < births:
        gaps.append((stamps[idx] - stamps[idx - 1]) / 60000000.0)
        idx += 1
    base["gaps"] = gaps
    under = 0
    for gap in gaps:
        if gap < 30.0:
            under += 1
    base["under_30m"] = under
    tight = False
    for gap in gaps:
        if gap < TIGHT_GAP_MINUTES:
            tight = True
    if births >= COUNT_DRIFT_THRESHOLD:
        base["verdict"] = CHURN
        base["reason"] = "count>=3-in-window"
        return base
    if births >= 2 and tight:
        base["verdict"] = CHURN
        base["reason"] = "tight-gap-rebirth"
        return base
    base["verdict"] = STEADY
    base["reason"] = "cadence-nominal"
    return base


def sample_text(result: dict) -> str:
    """One-line cadence shape for the resolver: counts, gaps, drift clause."""
    births = result.get("births", 0)
    gaps = result.get("gaps", [])
    under = result.get("under_30m", 0)
    gap_text = ",".join(f"{gap:.1f}m" for gap in gaps) if gaps else "none"
    text = f"births={births} gaps=[{gap_text}] under_30m={under}"
    if result.get("verdict") == CHURN:
        text += f" {result.get('reason', 'churn')}"
    return text


def decode_journal_entries(raw, boot, identifier, lower, upper, seen=None) -> list | None:
    """Decoded (epoch_us, MESSAGE) pairs from journalctl JSON output, or None.

    This is the textual protocol boundary for the restart sense: journalctl
    emits one JSON object per line with ``__CURSOR``, ``__REALTIME_TIMESTAMP``,
    ``_BOOT_ID`` and ``SYSLOG_IDENTIFIER`` metadata plus the ``MESSAGE`` text.
    Entries with missing, duplicate-cursor, or malformed metadata read as a
    failed acquisition (None), never as a partial list: a partial list would
    undercount births and read churn as steady. Timestamps outside the
    caller-supplied microsecond window are dropped, not failed: journalctl
    ``--since`` / ``--until`` are inclusive approximations and edge lines are
    normal. Callers supply boot identity, identifier, window bounds, and an
    optional seen-cursor set so this function stays pure and testable without
    a live journal.
    """
    import json as _json
    import re as _re
    if not isinstance(raw, bytes):
        return None
    if not isinstance(boot, str) or not boot:
        return None
    if not isinstance(identifier, str) or not identifier:
        return None
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError:
        return None
    cursors: set = set(seen) if isinstance(seen, set) else set()
    entries: list = []
    try:
        for line in lines:
            record = _json.loads(line)
            if not isinstance(record, dict):
                return None
            cursor = record.get("__CURSOR")
            timestamp = record.get("__REALTIME_TIMESTAMP")
            if (not isinstance(cursor, str) or not cursor or cursor in cursors
                    or record.get("_BOOT_ID") != boot
                    or record.get("SYSLOG_IDENTIFIER") != identifier
                    or not isinstance(timestamp, str)
                    or not _re.fullmatch(r"[0-9]+", timestamp)):
                return None
            cursors.add(cursor)
            stamp = int(timestamp)
            if lower <= stamp <= upper:
                entries.append((stamp, record.get("MESSAGE")))
    except (ValueError, TypeError):
        return None
    return entries
