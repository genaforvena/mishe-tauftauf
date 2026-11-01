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


def _swap_fraction(cgroup: dict) -> str:
    swap, swap_max = cgroup.get("swap"), cgroup.get("swap_max")
    if isinstance(swap, int) and isinstance(swap_max, int) and swap_max > 0:
        return f"{swap / swap_max:.1%}"
    return "uncapped-or-unknown"


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
