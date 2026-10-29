#!/usr/bin/env python3
"""Detector: the coverage reading's routed signal expires with the row it names.

The plant publishes one coverage reading (``SITE/checks/initiative.json``): a
**rolling 24-hour per-role view** of recorded wakes, settlements and outcome
evidence. The review sensor (``initiative_watch``) reads that reading and turns
its per-role ``outcome_coverage`` into the routed condition
``unknown:outcomes:<role>``, which is what reaches a role's owner.

That reading is a window. An outcome event older than the window start is
skipped before its evidence is read (``outcome_events.classify_outcomes``), so
it can never become a coverage gap; the role's ``outcome_coverage`` is then
OBSERVED, the watch's condition disappears, and the incident ends. **The routed
lifetime of a broken row is therefore (24 h - the row's age)**: a row published
an hour before the window closes is routed for an hour, and a row that is
already older than the window is never routed at all.

Measured on the plant's own tape (2026-10-22, discover wake 87735):

* ``chat.log#75512`` (published 2026-10-21T00:10:31Z) was named by the notice
  at 2026-10-22T00:10:11Z. Its window closed 20 seconds later, so the
  condition was gone before its own escalation deadline (00:20:11Z): the
  escalation never went out and the incident ended silently.
* ``chat.log#74505`` was named by the notice at 2026-10-21T22:11:47Z and was
  gone from the notice by 2026-10-22T00:10:11Z, with no repair in between.

The plant keeps a second, unbounded reading of the same rows: the standing
audit in ``sense.ledger.evidence-binding``, whose ``violations`` list names
every violating row with its role, its time and its classes, for the whole
post-clause history. So the loss does not disappear when the window closes --
it moves from a routed condition to a standing row nobody is asked about.

This check reads published artifacts and nothing else, and it re-derives
nothing:

* the plant's published coverage report (``SITE/checks/initiative.json``), read
  for its window and for the rows it names broken;
* the plant's published ledger reading (``SITE/discovery/latest.json``, and the
  newest ``scan-*.json`` when the former is absent), read for its standing
  violations and for its **gate**: the rows the plant itself still owes, which
  the ledger publishes as ``owed_rows``/``owed_roles``/``owed_detail``.

It does **not** decide whether an aged-out row is still owed. Genome ruled
(DM 85888) that the ledger is the sole band decider and that a second reader
may not re-derive a band, so this check reads the plant's own gate when the
published reading carries it and says UNKNOWN when it does not. A check that
re-derived the band here would be a second decider, whatever it is called.

Exit status:
  0  every published broken row keeps its routed visibility beyond the horizon,
     no role reads green while the published gate owes it, and the rows that
     aged out of the routed window are rows the published gate does not owe
  2  a published broken row's routed visibility ends within the horizon, or the
     published gate says the plant still owes a role whose coverage reading
     reads OBSERVED or is absent: the loss is no longer routed, and the plant
     knows it
  3  the question cannot be decided: no published reading, an unreadable or
     stale one, or a reading that publishes no gate, so whether the aged-out
     rows are still owed is UNKNOWN rather than clean
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: The published coverage reading: the plant's own per-role view.
REPORT_NAME = "checks/initiative.json"

#: The published ledger reading: the plant's standing audit of the same rows.
LEDGER_NAME = "discovery/latest.json"

#: How old a published reading may be before the check cannot decide.
DEFAULT_MAX_AGE = 900.0

#: How close a published broken row's routed visibility may come to its end
#: before the check calls it out.
DEFAULT_HORIZON_HOURS = 6.0

#: A standing violation, as the ledger publishes it: role@time:path(CLASSES).
VIOLATION = re.compile(
    r"^(?P<role>[^@]+)@(?P<time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)"
    r":(?P<path>.+)\((?P<classes>[A-Z]+)\)$"
)

#: The classes the coverage reader itself can produce: an ineligible path (A),
#: changed bytes (B) and a missing file (C). Class D alone is a citation, which
#: the coverage reader does not turn into a gap.
COVERAGE_CLASSES = frozenset("ABC")

#: The classes that name a loss of evidence: the bytes moved (B) or the file is
#: gone (C). A path-class-only row is a binding defect, not lost work.
LOSS_CLASSES = frozenset("BC")


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def _read_json(path: Path) -> dict:
    """A JSON object at path, or the reason it cannot be read."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Undecidable("%s cannot be read: %s" % (path.name, exc)) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise Undecidable("%s is not readable JSON: %s" % (path.name, exc)) from exc
    if not isinstance(payload, dict):
        raise Undecidable("%s is not a JSON object" % path.name)
    return payload


def _time(value: object) -> datetime | None:
    """An aware datetime from an ISO stamp, or None when it cannot be read."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _fresh(path: Path, max_age: float, now: datetime) -> dict:
    """A published reading that is present, readable and fresh enough to use."""
    payload = _read_json(path)
    generated = _time(payload.get("generated_at") or payload.get("created"))
    if generated is None:
        raise Undecidable("%s carries no readable generation stamp" % path.name)
    age = (now - generated).total_seconds()
    if age > max_age:
        raise Undecidable("%s is stale (%ds old)" % (path.name, int(age)))
    return payload


def _ledger_reading(home: Path, max_age: float, now: datetime) -> tuple[dict, dict]:
    """The published ledger reading and its source snapshot, preferring scan corpus.

    Returns (observation, snapshot_payload) where observation is the
    ``sense.ledger.evidence-binding`` dict and the snapshot is the scan
    JSON (or latest.json fallback) that produced it — so callers can read
    the receipt's top-level ``scan_window_aoi`` without re-scanning the
    corpus.
    """
    candidates = [home / LEDGER_NAME]
    scans = sorted((home / "discovery").glob("scan-*.json"))
    if scans:
        candidates.append(scans[-1])
    errors = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = _fresh(candidate, max_age, now)
        except Undecidable as exc:
            errors.append(str(exc))
            continue
        for observation in payload.get("observations") or []:
            if isinstance(observation, dict) and observation.get("id") == "sense.ledger.evidence-binding":
                return observation, payload
        errors.append("%s publishes no sense.ledger.evidence-binding" % candidate.name)
    raise Undecidable("; ".join(errors) or "no published ledger reading")


def _published_rows(report: dict) -> list[dict]:
    """Every outcome row the published coverage reading names broken."""
    rows = []
    for role, entry in sorted((report.get("roles") or {}).items()):
        for gap in entry.get("outcome_gaps") or []:
            if not isinstance(gap, dict):
                continue
            rows.append({
                "role": role,
                "ref": gap.get("ref"),
                "timestamp": gap.get("timestamp"),
                "reason": gap.get("reason"),
                "evidence": (gap.get("evidence") or {}).get("path"),
                "re_evidenced_by": gap.get("re_evidenced_by"),
            })
    return rows


def _standing_rows(observation: dict) -> tuple[list[dict], list[str]]:
    """The ledger's standing violations, and the violations it cannot parse."""
    rows, unparsed = [], []
    for entry in observation.get("violations") or []:
        if not isinstance(entry, str):
            unparsed.append(str(entry))
            continue
        match = VIOLATION.match(entry)
        if match is None:
            unparsed.append(entry)
            continue
        rows.append({
            "role": match.group("role"),
            "timestamp": match.group("time"),
            "path": match.group("path"),
            "classes": match.group("classes"),
        })
    return rows, unparsed


def _gate(observation: dict) -> dict | None:
    """The plant's own published gate: the rows it still owes, when published."""
    detail = observation.get("owed_detail")
    roles = observation.get("owed_roles")
    if not isinstance(detail, list) or not isinstance(roles, dict):
        return None
    return {"detail": [row for row in detail if isinstance(row, dict)], "roles": roles}


def _owed_paths(gate: dict) -> set[tuple[str, str]]:
    """The (role, evidence path) pairs the published gate still owes."""
    pairs = set()
    for row in gate["detail"]:
        role, path = row.get("role"), row.get("evidence")
        if isinstance(role, str) and isinstance(path, str):
            pairs.add((role, path))
    return pairs


def _report(home: Path, max_age: float, horizon_hours: float, now: datetime) -> int:
    """Decide the expiry property; return the exit status."""
    report = _fresh(home / REPORT_NAME, max_age, now)
    window = report.get("window") or {}
    start = _time(window.get("start"))
    seconds = window.get("seconds")
    if start is None or not isinstance(seconds, int) or seconds <= 0:
        raise Undecidable("the coverage report publishes no usable window")

    observation, _ledger_snapshot = _ledger_reading(home, max_age, now)
    standing, unparsed = _standing_rows(observation)
    gate = _gate(observation)

    published = _published_rows(report)
    # Windowed AoI (Yates Eq.2-3 / §II-B) beside the point _fresh check:
    # scan receipt publishes scan_window_aoi at top level (prior created
    # stamps as the sawtooth, now = that receipt's created). Emit it
    # beside the threshold lines; warmup (<2 priors) stays UNKNOWN — same
    # unknown vocabulary as _fresh's Undecidable.
    try:
        _aoi = None
        if isinstance(_ledger_snapshot, dict) and isinstance(_ledger_snapshot.get("scan_window_aoi"), dict):
            _aoi = _ledger_snapshot.get("scan_window_aoi")
        elif isinstance(report, dict) and isinstance(report.get("scan_window_aoi"), dict):
            _aoi = report.get("scan_window_aoi")
        elif isinstance(observation, dict) and isinstance(observation.get("scan_window_aoi"), dict):
            _aoi = observation.get("scan_window_aoi")
        if isinstance(_aoi, dict):
            print(
                "aoi       : current=%.1fs mean_age=%.1fs mean_peak=%.1fs p95=%.1fs max=%.1fs min=%.1fs n=%d window=%ds"
                % (
                    float(_aoi.get("current", 0)),
                    float(_aoi.get("mean_age", 0)),
                    float(_aoi.get("mean_peak", 0)),
                    float(_aoi.get("p95", 0)),
                    float(_aoi.get("max", 0)),
                    float(_aoi.get("min", 0)),
                    int(_aoi.get("n", 0)),
                    int(float(_aoi.get("window_seconds", 0))),
                )
            )
        else:
            print("aoi       : UNKNOWN — warmup (<2 prior scans) or unpublished window")
    except Exception as _aoi_exc:
        print("aoi       : UNKNOWN — %s" % _aoi_exc)
    print("report    : %s" % (home / REPORT_NAME))
    print("window    : %s .. %s (%ds)" % (window.get("start"), window.get("end"), seconds))
    print("published : %d broken row(s) the routed reading names" % len(published))

    horizon = horizon_hours * 3600.0
    expiring = []
    for row in published:
        stamp = _time(row["timestamp"])
        if stamp is None:
            print("UNKNOWN   : %s %s carries no readable time" % (row["role"], row["ref"]))
            continue
        expiry = stamp + timedelta(seconds=seconds)
        left = (expiry - now).total_seconds()
        row["expiry"] = expiry
        row["left"] = left
        if left <= horizon:
            expiring.append(row)
    for row in sorted(published, key=lambda item: item.get("left", 0.0)):
        if "left" not in row:
            continue
        print("%-9s: %s %s routed visibility ends %s (in %dm)%s"
              % ("EXPIRING" if row["left"] <= horizon else "ROUTED", row["role"], row["ref"],
                 row["expiry"].isoformat(), int(row["left"] // 60),
                 " [re-evidenced by %s]" % row["re_evidenced_by"] if row["re_evidenced_by"] else ""))

    aged = []
    for row in standing:
        stamp = _time(row["timestamp"])
        if stamp is None or stamp >= start:
            continue
        if not set(row["classes"]) & COVERAGE_CLASSES:
            continue
        aged.append(row)
    lost = [row for row in aged if set(row["classes"]) & LOSS_CLASSES]
    print("standing  : %d ledger violation(s), %d of them aged out of the routed window"
          % (len(standing), len(aged)))
    print("            of those, %d carry a byte or retention class (a loss the routed "
          "reading once named) and %d carry only the path class" % (len(lost), len(aged) - len(lost)))

    roles = report.get("roles") or {}
    owed = _owed_paths(gate) if gate else set()
    per_role = {}
    for row in aged:
        entry = per_role.setdefault(row["role"], {"aged": 0, "lost": 0, "owed": 0})
        entry["aged"] += 1
        if set(row["classes"]) & LOSS_CLASSES:
            entry["lost"] += 1
        if (row["role"], row["path"]) in owed:
            entry["owed"] += 1
    for role, entry in sorted(per_role.items()):
        coverage = (roles.get(role) or {}).get("outcome_coverage")
        print("AGED OUT  : %s holds %d aged-out row(s) (%d of them a byte or retention loss); "
              "published coverage=%s; the published gate owes %d"
              % (role, entry["aged"], entry["lost"], coverage, entry["owed"]))
    for row in sorted(aged, key=lambda item: item["timestamp"], reverse=True)[:12]:
        print("  aged    : %s %s %s (%s) owed=%s"
              % (row["role"], row["timestamp"], row["path"], row["classes"],
                 "yes" if (row["role"], row["path"]) in owed else
                 ("no" if gate else "UNKNOWN")))

    green = []
    if gate:
        for role, count in sorted(gate["roles"].items()):
            if not count:
                continue
            coverage = (roles.get(role) or {}).get("outcome_coverage")
            if coverage in (None, "OBSERVED"):
                green.append(role)
        if green:
            print("GREEN BY AGE: %s read(s) OBSERVED or absent while the published gate still "
                  "owes them" % ", ".join(green))
        print("gate      : the published reading owes %s row(s) over %s"
              % (observation.get("owed_rows"), ", ".join(sorted(gate["roles"])) or "no role"))
    else:
        print("gate      : UNPUBLISHED -- whether an aged-out row is still owed is UNKNOWN")
    if unparsed:
        print("UNKNOWN   : %d ledger violation line(s) could not be parsed" % len(unparsed))

    if expiring:
        print("verdict   : %d published broken row(s) lose their routed visibility within %dh; "
              "%d standing row(s) have aged out of the routed window"
              % (len(expiring), int(horizon_hours), len(aged)))
        return 2
    if green:
        print("verdict   : %d role(s) read OBSERVED or absent while the published gate still owes "
              "them; %d standing row(s) have aged out of the routed window" % (len(green), len(aged)))
        return 2
    if aged and not gate:
        print("verdict   : no published broken row is within the horizon, but %d standing row(s) "
              "have aged out of the routed window and the published reading publishes no gate, "
              "so whether they are still owed is UNKNOWN" % len(aged))
        return 3
    if aged:
        print("verdict   : %d standing row(s) have aged out of the routed window; the published "
              "gate owes none of them" % len(aged))
        return 0
    print("verdict   : every broken row keeps its routed visibility and no row has aged out")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, help="the site home to read")
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE,
                        help="how old a published reading may be, in seconds")
    parser.add_argument("--horizon-hours", type=float, default=DEFAULT_HORIZON_HOURS,
                        help="how close a broken row's routed visibility may come to its end")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        return _report(Path(args.home), args.max_age, args.horizon_hours, now)
    except Undecidable as exc:
        print("UNKNOWN   : %s" % exc)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
