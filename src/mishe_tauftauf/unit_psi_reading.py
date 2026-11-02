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

Liveness: the sampler should be `active/running`. The sense is a composite
tape+sampler claim: a fresh tape alone does not prove the recorder is running.
When the sampler is deterministically inactive, dead or missing, a fresh tape
reads UNKNOWN with a `sampler-not-active` cause. When the sampler state cannot
be read (no systemctl, timeout, error) a fresh tape also reads UNKNOWN with a
`sampler-unknown` cause — missing monitoring stays UNKNOWN. A stale tape already
reads `stale` (non-verified) and carries the same sampler text. All paths keep
malformed counts and the original tape signal after the cause so existing
consumers stay parsable.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .unit_psi_text import parse_jsonl_records, parse_systemd_show

SENSE_ID = "sense.proc.unit-psi"
SAMPLE_INTERVAL_S = 60
MAX_AGE_S = 180
WINDOW_S = 300
TAIL_BYTES = 262144
SAMPLER_UNIT = "mishe-unit-psi-sampler.service"


def _sampler_liveness(unit: str = SAMPLER_UNIT) -> dict | None:
    """Return systemd show properties for `unit`, or None when unreadable.

    A returned dict contains at least ActiveState/SubState/LoadState. None means
    the check itself is unavailable, not that the sampler is calm — the caller
    must keep the tape-derived state in that case.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit,
             "--property=ActiveState", "--property=SubState",
             "--property=LoadState", "--property=UnitFileState"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return None
        props = parse_systemd_show(result.stdout)
        return props if props else None
    except Exception:
        return None


def _sampler_text(props: dict | None) -> str:
    if props is None:
        return "sampler=unknown"
    load = props.get("LoadState", "")
    if load == "not-found":
        return "sampler=missing(no-unit-file)"
    active = props.get("ActiveState", "unknown")
    sub = props.get("SubState", "unknown")
    return f"sampler={active}/{sub}"


def _sampler_is_active(props: dict | None) -> bool | None:
    """True if deterministically active/running, False if deterministically not, None if unknown."""
    if props is None:
        return None
    if props.get("LoadState") == "not-found":
        return False
    if props.get("ActiveState") != "active":
        return False
    if props.get("SubState") != "running":
        # Active but not running is not healthy; treat as not-active so a
        # fresh tape does not read verified on a non-running recorder.
        return False
    return True


def _tail_records(path: Path) -> tuple[list[dict], int]:
    """Return parsed records from the file tail and the count of malformed lines."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > TAIL_BYTES:
            start = size - TAIL_BYTES
            handle.seek(start - 1)
            landed_on_line_start = handle.read(1) == b"\n"
            if not landed_on_line_start:
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


def _some_total_us(cgroup: dict) -> int | None:
    """Cumulative psi.some stall in microseconds; exact, unlike the 2-decimal EWMA avg60."""
    value = _nested(cgroup, "psi", "some", "total")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


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


AT_CAP_FRACTION = 0.99


def swap_at_cap(cgroup: dict) -> str:
    """"yes" when swap >= AT_CAP_FRACTION of a finite swap.max, "no" below it,
    "uncapped" for the sampler's "max" string, "unknown" when swap or a usable cap is missing."""
    if not isinstance(cgroup, dict):
        return "unknown"
    swap = _num_or_none(cgroup.get("swap"))
    swap_max = cgroup.get("swap_max")
    if swap is None:
        return "unknown"
    if swap_max == "max":
        return "uncapped"
    cap = _num_or_none(swap_max)
    if cap is None or cap <= 0:
        return "unknown"
    return "yes" if swap >= AT_CAP_FRACTION * cap else "no"


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
            "some_total_us": _some_total_us(cgroup),
            "high_delta": delta("high"),
            "max_delta": delta("max"),
            "swap_bytes": swap_bytes,
            "swap_of_max": swap_frac,
        })

    with_value = [r for r in rows if r["full_avg60"] is not None]
    # Zero readings carry no ranking information: the 2-decimal EWMA rounds short stalls
    # to 0.0, so zeros are counted, never published as a top list.
    ranked = sorted((r for r in with_value if r["full_avg60"] > 0),
                    key=lambda r: r["full_avg60"], reverse=True)
    zero_rows = [r for r in with_value if r["full_avg60"] == 0]
    return {
        "state": "verified" if with_value else "unknown",
        "at": at,
        "span_s": span,
        "units_total": len(rows),
        "units_with_full_avg60": len(with_value),
        "units_full_avg60_zero": len(zero_rows),
        "units_with_stall_total_but_zero_avg60": sum(
            1 for r in zero_rows if (r["some_total_us"] or 0) > 0),
        "top_full_avg60": ranked[:top],
        "high_or_max_moved": sorted(
            (r for r in rows if (r["high_delta"] or 0) > 0 or (r["max_delta"] or 0) > 0),
            key=lambda r: (r["high_delta"] or 0) + (r["max_delta"] or 0), reverse=True),
        "swap_units": sorted((r for r in rows if r["swap_bytes"]),
                             key=lambda r: r["swap_bytes"], reverse=True)[:top],
    }


STALL_SKEW_TOLERANCE_US = 1_000_000


def _ancestors(name: str) -> list[str]:
    return [name[:i] for i, ch in enumerate(name) if ch == "/"]


def stall_inversions(stall_us: dict[str, int]) -> set[str]:
    """Units involved in a hierarchy break: a child's some-stall delta above an ancestor's
    over the same interval, beyond STALL_SKEW_TOLERANCE_US.

    Basis is an assumption, not a cited kernel rule: the check presumes a stall is charged
    to the cgroup and every ancestor. The cgroup v2 psi doc does not state that, and the
    kernel source was not checked here. Measured on operator/unit-psi.jsonl (194 rows,
    2026-11-01): no child-minus-ancestor cumulative some-total was ever positive across 695
    pairs, but a single-minute delta exceeded its parent's by up to 26 s in 16 of 193
    intervals, all under mishe-lifeline and tmux scopes. Records are read unit by unit, so
    that is read-order skew, not a proven break. Treat a returned set as a skew-sensitive
    signal, not as proof of a hierarchy fault. Both sides of a break are returned; neither
    stall can be attributed to one unit."""
    bad: set[str] = set()
    for child, child_delta in stall_us.items():
        for anc in _ancestors(child):
            if anc in stall_us and child_delta > stall_us[anc] + STALL_SKEW_TOLERANCE_US:
                bad.add(child)
                bad.add(anc)
    return bad


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


def _top_stall_text(stall_us: dict[str, int], inverted: set[str], span: float,
                    has_window: bool) -> str:
    """Stall leader over the window, independent of the high-event leader.

    Share is the kernel's own measure: stall time / wall time over the span, from the
    exact psi.some.total counter. Printed with 4 decimals in percent (0.0001% grain).
    """
    if not has_window or span <= 0:
        return "top-stall=unknown(no-window)"
    if not stall_us:
        return "top-stall=unknown"
    unit, delta = max(stall_us.items(), key=lambda kv: kv[1])
    if delta <= 0:
        return f"top-stall=none/{span:.0f}s"
    if unit in inverted:
        return "top-stall=unknown(hierarchy-inverted)"
    share = 100.0 * delta / (1_000_000 * span)
    return f"top-stall={unit}:{share:.4f}%/{span:.0f}s"


def read_unit_psi(path, *, now: float) -> dict:
    """Sense dict (id, state, sample, kind, validity, source) from the sampler JSONL at `path`."""
    source = str(path)
    base = {"id": SENSE_ID, "kind": "read", "source": source,
            "validity_s": MAX_AGE_S}
    sampler_props = _sampler_liveness()
    sampler_text = _sampler_text(sampler_props)
    sampler_active = _sampler_is_active(sampler_props)

    def _finalize(state: str, sample: str) -> dict:
        # Always carry the liveness text. A fresh verified tape requires a
        # deterministically active/running recorder; otherwise the composite
        # claim is unknown. Stale stays stale to preserve its own evidence;
        # unknown/missing tape stays unknown. This separates tape freshness
        # (age) from recorder liveness (systemd state) as steward 200070
        # requires: fresh data cannot prove the process is running.
        full_sample = f"{sample} {sampler_text}"
        if state == "verified" and sampler_active is not True:
            if sampler_active is None:
                return {**base, "state": "unknown",
                        "sample": f"sampler-unknown({sampler_text}): {full_sample}"}
            return {**base, "state": "unknown",
                    "sample": f"sampler-not-active({sampler_text}): {full_sample}"}
        return {**base, "state": state, "sample": full_sample}

    path = Path(path)
    try:
        if not path.is_file():
            return _finalize("unknown", "sampler output absent")
        records, malformed = _tail_records(path)
    except OSError as exc:
        return _finalize("unknown", f"sampler read failed: {exc.__class__.__name__}")
    if not records:
        return _finalize("unknown", f"no parseable record; malformed={malformed}")

    records.sort(key=lambda r: r["at"])
    latest = records[-1]
    age = now - float(latest["at"])
    units = latest["cgroups"]
    if not units:
        return _finalize("unknown", f"newest record has no cgroup entries age={age:.0f}s malformed={malformed}")

    # Per-unit high-event delta over the window; the newest record must anchor it.
    earlier = [r for r in records[:-1] if float(latest["at"]) - float(r["at"]) <= WINDOW_S]
    deltas: dict[str, int] = {}
    stall_us: dict[str, int] = {}
    if earlier:
        anchor = earlier[0]
        span = float(latest["at"]) - float(anchor["at"])
        for name, cgroup in units.items():
            now_high = _high_events(cgroup)
            then = anchor["cgroups"].get(name)
            then_high = _high_events(then) if isinstance(then, dict) else None
            if now_high is not None and then_high is not None:
                deltas[name] = now_high - then_high
            now_stall = _some_total_us(cgroup)
            then_stall = _some_total_us(then) if isinstance(then, dict) else None
            if now_stall is not None and then_stall is not None:
                stall_us[name] = now_stall - then_stall
    else:
        span = 0.0

    plants = units.get("mishe-plants.slice")
    plant_some = _some_avg10(plants) if isinstance(plants, dict) else None
    unit_rows = [(name, _some_avg10(c)) for name, c in units.items() if isinstance(c, dict)]
    unit_rows = [(n, v) for n, v in unit_rows if v is not None]
    if not unit_rows:
        return _finalize("unknown", f"no unit carries psi.some.avg10 age={age:.0f}s malformed={malformed}")

    inverted = stall_inversions(stall_us)
    if stall_us:
        hierarchy_text = (f"stall-hierarchy=inverted:{','.join(sorted(inverted))}" if inverted
                          else "stall-hierarchy=ok")
    else:
        hierarchy_text = "stall-hierarchy=unchecked"
    top_high = sorted(deltas.items(), key=lambda kv: kv[1], reverse=True)[:1]
    if top_high and top_high[0][1] > 0:
        unit = top_high[0][0]
        stall = stall_us.get(unit)
        if unit in inverted:
            stall_text = " some-stall=unknown(hierarchy-inverted)"
        elif stall is not None:
            stall_text = f" some-stall=+{stall / 1000:.1f}ms"
        else:
            stall_text = " some-stall=unknown"
        high_text = f"top-high-delta={top_high[0][0]}:+{top_high[0][1]}/{span:.0f}s{stall_text}"
    else:
        high_text = f"high-delta=0/{span:.0f}s" if deltas else "high-delta=unknown(no-window)"
    stall_text = _top_stall_text(stall_us, inverted, span, bool(earlier))
    top_some_name, top_some_value = max(unit_rows, key=lambda kv: kv[1])
    some_text = (f"top-some={top_some_name}:{top_some_value:.2f}" if top_some_value > 0
                 else "top-some=all-zero")
    plant_text = f"plants-some-avg10={plant_some:.2f}" if plant_some is not None else "plants-some-avg10=unknown"
    sample = (f"units={len(units)} psi-units={len(unit_rows)} age={age:.0f}s "
              f"{plant_text} {some_text} {stall_text} {high_text} "
              f"swap-plants={_swap_fraction(plants) if isinstance(plants, dict) else 'unknown'} "
              f"swap-plants-at-cap>={AT_CAP_FRACTION:g}={swap_at_cap(plants)} "
              f"{hierarchy_text} malformed={malformed}")
    if age < 0 or age > MAX_AGE_S:
        return _finalize("stale", f"historical: {sample}")
    return _finalize("verified", sample)
