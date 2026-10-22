#!/usr/bin/env python3
"""Detector: every broken coverage row has a ready remedy, and the reading can apply it.

The plant publishes one coverage reading (``SITE/checks/initiative.json``).
For every outcome row it classified, that reading says whether the bound
evidence file still holds the bytes the outcome published. A row whose bytes
moved is a **broken row**: the outcome's evidence is no longer verifiable, so
the role's outcome coverage is UNKNOWN and the review sensor routes it.

The plant already has a remedy for that case. A later outcome of the *same*
role whose evidence path is the lost artifact's own stem plus a remedy suffix
(``-v2``, ``-final``, ``-final-v2``) in the same directory re-evidences the
lost row, and the coverage reader recognises the relation. That relation is
decidable from paths alone, so a reader can apply it without reading prose.

Two things can go wrong, and this check reads them apart:

* **unremedied** -- the row is broken and no published outcome re-evidences
  it. The owner has one bounded action left: publish a write-once copy of the
  artifact under its own stem plus a remedy suffix, and cite that copy.
  This check names the path to create, and says whether the lost bytes are
  still on disk to copy from.
* **reading behind** -- the row *is* re-evidenced, but the published reading
  still reports it as broken. Then the running reader cannot apply the
  plant's own remedy rule, and every re-evidence stays invisible live no
  matter how correctly it was published.

Measured on 2026-10-22 over the live site home: the published reading named
11 broken rows, of which 9 were already re-evidenced and 2 were not. The
reading that produced it runs from a bundle that predates the rule, so the 9
were reported broken anyway.

The check reads the published coverage report and the bound evidence files and
nothing else: no tape, no record store, no plant package. That is deliberate
-- a check that reads published fields cannot die because an internal helper
changed its signature, which is how a measurement script of discover's died
silently on 2026-10-21.

Exit status:
  0  every broken row is re-evidenced and the published reading applies the
     rule: nothing to repair
  2  at least one broken row has no published remedy (**unremedied**), or the
     published reading reports a re-evidenced row as broken
     (**reading-behind**): the plant's own reading is not what the plant's
     rules say
  3  the question cannot be decided: no coverage report, an unreadable or
     stale one, or one whose rows cannot be read
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

#: The published coverage report: the plant's own reading of every outcome row.
REPORT_NAME = "checks/initiative.json"

#: How old a published coverage report may be before the check cannot decide.
DEFAULT_MAX_AGE = 600.0

#: The plant's remedy naming vocabulary: the lost artifact's stem plus one of
#: these suffixes, in the lost artifact's own directory. It mirrors the
#: plant's ``EVIDENCE_REMEDY_SUFFIX``; the check does not import the package,
#: so a change to the plant's vocabulary makes a re-evidenced row read as
#: unremedied here -- loud, not silent.
REMEDY_SUFFIX = re.compile(r"^(?P<stem>.+?)-(?P<sfx>v\d+|final|final-v\d+)$")

#: The suffix this check proposes when no remedy is published yet.
PROPOSED_SUFFIX = "final"


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def _read_json(path: Path) -> dict:
    """A JSON object at path, or the reason it cannot be read."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Undecidable("the coverage report cannot be read: %s" % exc) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise Undecidable("the coverage report is not readable JSON: %s" % exc) from exc
    if not isinstance(payload, dict):
        raise Undecidable("the coverage report is not a JSON object")
    return payload


def _time(value: object) -> datetime | None:
    """An aware datetime from an ISO stamp, or None when it cannot be read."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _age(stamp: object) -> float | None:
    """Seconds since an ISO stamp, or None when the stamp cannot be read."""
    parsed = _time(stamp)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _digest(path: Path) -> str | None:
    """The sha256 of a file's bytes, or None when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _resolve(home: Path, bound: str) -> Path:
    """The file a bound evidence path names, as an absolute path."""
    target = Path(bound)
    if target.is_absolute():
        return target
    return home / target


def remedy_stem(value: object) -> str | None:
    """The lost artifact's stem when the named path is its corrected form."""
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    match = REMEDY_SUFFIX.match(Path(text).stem)
    return match.group("stem") if match else None


def is_remedy(lost: Path, successor: Path) -> bool:
    """Whether the successor names the lost artifact's corrected form."""
    stem = remedy_stem(successor)
    return stem is not None and stem == lost.stem and successor.parent == lost.parent


def report_items(report: dict) -> list[dict]:
    """Every outcome row the published report carries, with its binding."""
    outcomes = report.get("outcomes")
    if not isinstance(outcomes, dict):
        raise Undecidable("the coverage report carries no outcomes section")
    items = outcomes.get("items")
    if not isinstance(items, list):
        raise Undecidable("the coverage report carries no outcome rows")
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            continue
        bound = evidence.get("path")
        digest = evidence.get("sha256")
        if not isinstance(bound, str) or not bound:
            continue
        rows.append({"role": str(item.get("role") or "?"),
                     "sequence": item.get("sequence"),
                     "timestamp": item.get("timestamp"),
                     "path": bound,
                     "sha256": digest if isinstance(digest, str) else "",
                     "state": item.get("state"),
                     "link_kind": item.get("link_kind"),
                     "re_evidenced_by": item.get("re_evidenced_by")})
    return rows


def order(row: dict) -> tuple:
    """A row's own order: its timestamp, with the tape sequence as tie-break."""
    stamp = _time(row.get("timestamp"))
    sequence = row.get("sequence")
    return (stamp.timestamp() if stamp else 0.0,
            sequence if isinstance(sequence, int) else 0)


def broken_rows(home: Path, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """The rows whose bound bytes moved, and the rows whose bytes are intact."""
    broken, intact = [], []
    for row in rows:
        path = _resolve(home, row["path"])
        if _digest(path) == row["sha256"]:
            intact.append(row)
        else:
            broken.append(row)
    return broken, intact


def remedy_for(broken: dict, rows: list[dict]) -> dict | None:
    """The later same-role row that re-evidences a broken row, if there is one."""
    lost = Path(broken["path"])
    for candidate in rows:
        if candidate is broken:
            continue
        if candidate["role"] != broken["role"]:
            continue
        if order(candidate) <= order(broken):
            continue
        if not is_remedy(lost, Path(candidate["path"])):
            continue
        return candidate
    return None


def proposed_path(broken: dict, rows: list[dict]) -> Path:
    """The path a remedy successor would take, and the first free name.

    A name is free when no published row binds it and no file holds it: a
    successor that overwrites an artifact would destroy the evidence the
    remedy is meant to preserve.
    """
    lost = Path(broken["path"])
    taken = {Path(row["path"]).name for row in rows}
    for suffix in (PROPOSED_SUFFIX, "v2", "v3", "final-v2"):
        name = lost.stem + "-" + suffix + lost.suffix
        if name in taken or lost.with_name(name).exists():
            continue
        return lost.with_name(name)
    return lost.with_name(lost.stem + "-v2" + lost.suffix)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE,
                        help="seconds after which the published coverage report is stale")
    args = parser.parse_args()
    home = args.home.resolve()

    try:
        report = _read_json(home / REPORT_NAME)
        rows = report_items(report)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3

    age = _age(report.get("generated_at"))
    if age is None:
        print("UNKNOWN — the coverage report carries no generation timestamp")
        return 3
    if age > args.max_age:
        print("UNKNOWN — the coverage report is stale (%d s old, limit %d s)"
              % (int(age), int(args.max_age)))
        return 3

    broken, intact = broken_rows(home, rows)
    unremedied, behind, remedied = [], [], []
    for row in broken:
        successor = remedy_for(row, intact)
        if successor is None:
            unremedied.append(row)
            continue
        remedied.append((row, successor))
        if row.get("state") == "OBSERVED" and row.get("link_kind"):
            continue
        behind.append((row, successor))

    print("report    : %s (%d s old)" % (home / REPORT_NAME, int(age)))
    print("rows      : %d published, %d broken, %d intact" % (len(rows), len(broken), len(intact)))
    for row, successor in remedied:
        print("remedied  : seq=%s role=%s by %s" % (row.get("sequence"), row["role"],
                                                    successor.get("sequence")))
    for row in unremedied:
        lost = _resolve(home, row["path"])
        exists = lost.is_file()
        print("UNREMEDIED: seq=%s role=%s" % (row.get("sequence"), row["role"]))
        print("  bound   : %s" % row["path"])
        print("  bytes   : %s" % ("still on disk, copyable" if exists else "gone"))
        print("  remedy  : publish an outcome citing %s" % proposed_path(row, rows))
    for row, successor in behind:
        print("BEHIND    : seq=%s role=%s is re-evidenced by %s, but the published "
              "reading still reports it broken"
              % (row.get("sequence"), row["role"], successor.get("sequence")))

    if unremedied or behind:
        print("verdict   : %d unremedied row(s), %d row(s) the published reading "
              "reports broken although they are re-evidenced" % (len(unremedied), len(behind)))
        return 2
    print("verdict   : CONSISTENT — every broken row is re-evidenced and the "
          "published reading applies the rule")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
