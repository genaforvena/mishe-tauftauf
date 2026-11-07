"""Read-only accelerometer coverage of the Redmi10 observation stream.

The phone body logs ``acceleration x= y= z=`` rows while its accelerometer delivers,
and ``acceleration unavailable reason=stale age_ms=N`` rows while it does not. Those
stale stretches are real gaps: a stale window must never read as "still". This reader
reports how much of the recent window carries fresh, valid acceleration, and where the
longest gaps are. It never writes, starts or signals the body.

Validity: a 10-minute bin is covered only when it holds at least MIN_ROWS_PER_BIN valid
rows. The observation is verified only when the newest acceleration row is valid and
fresh. Otherwise it is UNKNOWN, and the coverage numbers are shown as labelled history.
"""
from __future__ import annotations

from pathlib import Path

from .redmi10_stream import DEFAULT_LOG, MAX_ROW_AGE_S, _tail_lines
from .redmi10_stream_text import parse_number, parse_row

SENSE_ID = "sense.body.redmi10.accel-coverage"
BIN_S = 600
MIN_ROWS_PER_BIN = 30  # the body logs about one row every 5 s while it delivers (about 120 per bin)
WINDOW_S = 3600 * 3
# Several hours of all kinds of rows fit in this tail; the reader stays bounded per discovery pass.
TAIL_BYTES = 4_000_000


def _valid_acceleration(fields: dict[str, str]) -> bool:
    return all(parse_number(fields.get(axis)) is not None for axis in ("x", "y", "z"))


def _bins_covered(valid_epochs: list[float], start: float, end: float) -> list[bool]:
    count = int((end - start) // BIN_S)
    tally = [0] * count
    for epoch in valid_epochs:
        if start <= epoch < end:
            tally[int((epoch - start) // BIN_S)] += 1
    return [n >= MIN_ROWS_PER_BIN for n in tally]


def _longest_gap_bins(covered: list[bool]) -> int:
    longest = run = 0
    for ok in covered:
        run = 0 if ok else run + 1
        longest = max(longest, run)
    return longest


def read_redmi10_accel_coverage(path: Path | str = DEFAULT_LOG, *, now: float) -> dict[str, object]:
    """Fresh-acceleration state plus window coverage for the Redmi10 stream.

    UNKNOWN when the log is unreadable, holds no acceleration row, or the newest
    acceleration row is unavailable or older than MAX_ROW_AGE_S.
    """
    base = {"id": SENSE_ID, "kind": "read", "source": str(path)}
    try:
        lines = _tail_lines(Path(path), TAIL_BYTES)
    except OSError as exc:
        return {**base, "state": "unknown", "sample": f"stream log unreadable: {exc.__class__.__name__}"}
    accel = [row for row in (parse_row(line) for line in lines) if row is not None and row["kind"] == "acceleration"]
    if not accel:
        return {**base, "state": "unknown", "sample": "no acceleration row in the stream tail"}
    valid_epochs = [row["epoch"] for row in accel if _valid_acceleration(row["fields"])]
    newest = max(accel, key=lambda row: row["epoch"])
    newest_valid = _valid_acceleration(newest["fields"])
    age = now - newest["epoch"]
    end = now
    start = end - WINDOW_S
    covered = _bins_covered(valid_epochs, start, end)
    coverage = (sum(covered) / len(covered)) if covered else 0.0
    gap_bins = _longest_gap_bins(covered)
    summary = (f"window={WINDOW_S // 3600}h covered_bins={sum(covered)}/{len(covered)} "
               f"coverage={coverage:.0%} longest_gap={gap_bins * BIN_S // 60}min")
    if not newest_valid:
        reason = newest["fields"].get("reason", "unavailable")
        return {**base, "state": "unknown",
                "sample": f"acceleration {reason}: newest row not valid; {summary}",
                "coverage": round(coverage, 3), "covered_bins": sum(covered), "bins": len(covered),
                "longest_gap_bins": gap_bins, "last_row_epoch": newest["epoch"]}
    if age > MAX_ROW_AGE_S or age < -MAX_ROW_AGE_S:
        return {**base, "state": "unknown",
                "sample": f"acceleration stale: newest valid row {age:.0f}s old; {summary}",
                "coverage": round(coverage, 3), "covered_bins": sum(covered), "bins": len(covered),
                "longest_gap_bins": gap_bins, "last_row_epoch": newest["epoch"]}
    return {**base, "state": "verified",
            "sample": f"acceleration fresh row_age={age:.0f}s; {summary}",
            "coverage": round(coverage, 3), "covered_bins": sum(covered), "bins": len(covered),
            "longest_gap_bins": gap_bins, "last_row_epoch": newest["epoch"], "row_age_s": round(age, 1)}
