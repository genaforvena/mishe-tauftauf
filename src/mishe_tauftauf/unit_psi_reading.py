"""Read-only per-unit pressure verdict from the operator's cgroup sampler JSONL.

The sampler (operator/unit_psi_sampler.py) appends one record per minute with
memory.pressure, memory.events and swap for every unit under the plant slices.
This reader turns the newest record into a sense. It writes nothing and touches
no limits.

Validity: a record older than MAX_AGE_S is historical evidence and reads UNKNOWN
for current claims. A missing file, no parseable record or no cgroup entries
reads UNKNOWN, never calm. Malformed lines are skipped and counted in the sample.
Event deltas use only records inside WINDOW_S of the newest record; with no
earlier record in the window the delta is reported as unknown, not zero.
"""
from __future__ import annotations

from pathlib import Path

from .unit_psi_text import parse_jsonl_records

SENSE_ID = "sense.proc.unit-psi"
SAMPLE_INTERVAL_S = 60
MAX_AGE_S = 180
WINDOW_S = 300
TAIL_BYTES = 262144


def _tail_records(path: Path) -> tuple[list[dict], int]:
    """Return parsed records from the file tail and the count of malformed lines."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > TAIL_BYTES:
            handle.seek(size - TAIL_BYTES)
            handle.readline()  # drop the partial line the seek landed inside
        raw = handle.read()
    return parse_jsonl_records(raw)


def _some_avg10(cgroup: dict) -> float | None:
    try:
        return float(cgroup["psi"]["some"]["avg10"])
    except (KeyError, TypeError, ValueError):
        return None


def _high_events(cgroup: dict) -> int | None:
    try:
        return int(cgroup["events"]["high"])
    except (KeyError, TypeError, ValueError):
        return None


def read_tail(path) -> tuple[list[dict], int]:
    """Public tail read for other views of the same sampler file (same TAIL_BYTES policy)."""
    return _tail_records(Path(path))


def _num_or_none(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def swap_of_max(cgroup: dict) -> tuple[float | None, str]:
    """(swap bytes or None, swap.max reading): a percentage, "uncapped" for the sampler's
    "max" string, or "unknown" when swap or swap.max is missing or is a zero cap."""
    swap = _num_or_none(cgroup.get("swap"))
    swap_max = cgroup.get("swap_max")
    if swap is None:
        return None, "unknown"
    if swap_max == "max":
        return swap, "uncapped"
    cap = _num_or_none(swap_max)
    if cap is None or cap <= 0:
        return swap, "unknown"
    return swap, f"{swap / cap:.1%}"


def _swap_fraction(cgroup: dict) -> str:
    return swap_of_max(cgroup)[1]


def attribute(records: list[dict], *, window_s: float = WINDOW_S, top: int = 3) -> dict:
    """Per-unit attribution for the newest record (pure; no I/O).

    Counter deltas use only an anchor record inside `window_s`; without one they are
    None, never zero. Units without psi.full.avg60 are not ranked.
    """
    if not records:
        return {"state": "unknown", "reason": "no records"}
    ordered = sorted(records, key=lambda r: float(r["at"]))
    latest = ordered[-1]
    at = float(latest["at"])
    units = latest.get("cgroups") or {}
    if not units:
        return {"state": "unknown", "reason": "newest record has no cgroup entries"}

    anchor = next((r for r in ordered[:-1] if at - float(r["at"]) <= window_s), None)
    span = (at - float(anchor["at"])) if anchor else None

    rows = []
    for name, cgroup in units.items():
        if not isinstance(cgroup, dict):
            continue
        then = anchor["cgroups"].get(name) if anchor else None
        then = then if isinstance(then, dict) else None

        def delta(counter):
            now_v = _high_or_max(cgroup, counter)
            then_v = _high_or_max(then, counter) if then is not None else None
            return None if now_v is None or then_v is None else now_v - then_v

        swap_bytes, swap_frac = swap_of_max(cgroup)
        rows.append({
            "unit": name,
            "full_avg60": _num_or_none(_nested(cgroup, "psi", "full", "avg60")),
            "some_avg60": _num_or_none(_nested(cgroup, "psi", "some", "avg60")),
            "high_delta": delta("high"),
            "max_delta": delta("max"),
            "swap_bytes": swap_bytes,
            "swap_of_max": swap_frac,
        })

    ranked = sorted((r for r in rows if r["full_avg60"] is not None),
                    key=lambda r: r["full_avg60"], reverse=True)
    return {
        "state": "verified" if ranked else "unknown",
        "at": at,
        "span_s": span,
        "units_total": len(rows),
        "units_with_full_avg60": len(ranked),
        "top_full_avg60": ranked[:top],
        "high_or_max_moved": sorted(
            (r for r in rows if (r["high_delta"] or 0) > 0 or (r["max_delta"] or 0) > 0),
            key=lambda r: (r["high_delta"] or 0) + (r["max_delta"] or 0), reverse=True),
        "swap_units": sorted((r for r in rows if r["swap_bytes"]),
                             key=lambda r: r["swap_bytes"], reverse=True)[:top],
    }


def _nested(mapping, *keys):
    cur = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _high_or_max(cgroup, counter: str):
    if not isinstance(cgroup, dict):
        return None
    value = _nested(cgroup, "events", counter)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def read_unit_psi(path, *, now: float) -> dict:
    """Sense dict (id, state, sample, kind, validity, source) from the sampler JSONL at `path`."""
    source = str(path)
    base = {"id": SENSE_ID, "kind": "read", "source": source,
            "validity_s": MAX_AGE_S}
    path = Path(path)
    try:
        if not path.is_file():
            return {**base, "state": "unknown", "sample": "sampler output absent"}
        records, malformed = _tail_records(path)
    except OSError as exc:
        return {**base, "state": "unknown", "sample": f"sampler read failed: {exc.__class__.__name__}"}
    if not records:
        return {**base, "state": "unknown", "sample": f"no parseable record; malformed={malformed}"}

    records.sort(key=lambda r: r["at"])
    latest = records[-1]
    age = now - float(latest["at"])
    units = latest["cgroups"]
    if not units:
        return {**base, "state": "unknown", "sample": "newest record has no cgroup entries"}

    # Per-unit high-event delta over the window; the newest record must anchor it.
    earlier = [r for r in records[:-1] if float(latest["at"]) - float(r["at"]) <= WINDOW_S]
    deltas: dict[str, int] = {}
    if earlier:
        anchor = earlier[0]
        span = float(latest["at"]) - float(anchor["at"])
        for name, cgroup in units.items():
            now_high = _high_events(cgroup)
            then = anchor["cgroups"].get(name)
            then_high = _high_events(then) if isinstance(then, dict) else None
            if now_high is not None and then_high is not None:
                deltas[name] = now_high - then_high
    else:
        span = 0.0

    plants = units.get("mishe-plants.slice")
    plant_some = _some_avg10(plants) if isinstance(plants, dict) else None
    unit_rows = [(name, _some_avg10(c)) for name, c in units.items() if isinstance(c, dict)]
    unit_rows = [(n, v) for n, v in unit_rows if v is not None]
    if not unit_rows:
        return {**base, "state": "unknown", "sample": "no unit carries psi.some.avg10"}

    top_high = sorted(deltas.items(), key=lambda kv: kv[1], reverse=True)[:1]
    high_text = (f"top-high-delta={top_high[0][0]}:+{top_high[0][1]}/{span:.0f}s"
                 if top_high and top_high[0][1] > 0 else
                 (f"high-delta=0/{span:.0f}s" if deltas else "high-delta=unknown(no-window)"))
    top_some = max(unit_rows, key=lambda kv: kv[1])
    plant_text = f"plants-some-avg10={plant_some:.2f}" if plant_some is not None else "plants-some-avg10=unknown"
    sample = (f"units={len(units)} psi-units={len(unit_rows)} age={age:.0f}s "
              f"{plant_text} top-some={top_some[0]}:{top_some[1]:.2f} {high_text} "
              f"swap-plants={_swap_fraction(plants) if isinstance(plants, dict) else 'unknown'} "
              f"malformed={malformed}")
    if age < 0 or age > MAX_AGE_S:
        return {**base, "state": "stale", "sample": f"historical: {sample}"}
    return {**base, "state": "verified", "sample": sample}
