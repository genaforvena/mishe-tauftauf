#!/usr/bin/env python3
"""Detector: a bound evidence file that is still writable is a row a later edit will drop.

The plant's clause makes a bound artifact **write-once** evidence: the outcome
records the file's digest, and the standing audit keeps the row forever. The
clause is enforced on *publication* — the writer refuses an ineligible path, a
symlink component, a lexical traversal and a path another outcome already
bound — but nothing makes the file itself read-only. So the file stays at
whatever mode its author left it in, usually ``0664``, and the write-once
property the clause states is a convention rather than a property of the bytes.

That convention is what cost the plant rows. Measured on the live site home at
2026-10-21T23:50Z: of the plant's **3191** post-clause bound outcomes, **3129**
name an evidence file that is still writable and only **57** name a write-once
one (``0444``); 5 name a file that no longer exists. Nine of the eleven
non-observed rows the live coverage reader reported were of exactly this class
— the artifact was edited after it was bound, so the bytes the outcome
published are permanently lost and the standing audit keeps the loss. The
remedy exists (a write-once copy under the lost artifact's own stem plus a
remedy suffix, cited in a new outcome) and costs a re-evidence; the cheaper
repair is to make the file read-only while it is still the file the outcome
published.

This check answers one question, from the plant's own published state: **which
bound evidence files can still be edited?** It does not ask whether any of them
*has* been edited — the coverage reader already reports that, per row, with the
reason — because the useful signal is the one that arrives first. A writable
bound file is a row at risk; a changed digest is a row lost.

It reads two published artifacts and nothing else:

* the plant's published coverage report (``SITE/checks/initiative.json``), read
  for the outcome rows it classified and the evidence path each one bound; and
* the published record store (``SITE/records/*.json``), read for the outcome
  publications the report's 24-hour window may not cover, so the check sees
  every binding the plant has made rather than only the recent ones.

It imports nothing from the plant package and calls no internal helper. That is
deliberate, for the reason the sibling check records: a measurement script of
mine died silently on 2026-10-21 when ``discovery._rebind_links`` changed its
return arity, because it asserted on an internal helper's signature. A check
that reads published fields cannot die that way. It also does not parse the
tape: the tape's outcome publications are decoded by the plant's own adapter,
and a second decoder here would be the second decider the plant's rulings
forbid.

**The two populations are reported apart, and the check says which is which.**
The coverage report's rows are the rows the plant's reader currently classifies.
The record store's rows are every binding the plant has ever published,
including those predating the write-once clause, which the standing audit does
not track. The verdict is about writability — a bound file that can be edited
will not match the digest its outcome recorded, whatever the clause's age — so
both populations are reported, and neither is presented as the other's.

The mode question is decided from the file's own bytes, not from a recorded
claim: a file is **writable** when any of its write bits is set
(``st_mode & 0o222``), which is exactly the condition under which the next
``open(..., "w")`` succeeds. The owner bit alone is enough, so the check does
not distinguish "only the owner may edit" from "the world may edit" — both are
files an edit can reach, and the plant is single-owner in any case.

Exit status:
  0  every bound evidence file the check could see is read-only
  2  at least one bound evidence file is still writable: that row can still be
     dropped by a later edit
  3  the question cannot be decided: no published coverage report, an
     unreadable or stale one, or no bound evidence path could be read
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

#: The published coverage report: the plant's own reading of every outcome row
#: it classified, each with the evidence path that row bound.
REPORT_NAME = "checks/initiative.json"

#: The published record store: one content-addressed file per published record.
RECORDS_DIR = "records"

#: How old a published coverage report may be before the check cannot decide.
DEFAULT_MAX_AGE = 3600.0

#: The record kind a published wall outcome carries.
OUTCOME_KIND = "wall-outcome"


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def _read_json(path: Path) -> dict:
    """A JSON object at path, or the reason it cannot be read."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Undecidable("no published file at " + str(path))
    except OSError as exc:
        raise Undecidable("the published file is unreadable: " + type(exc).__name__)
    try:
        payload = json.loads(raw)
    except ValueError:
        raise Undecidable("the published file is malformed: " + str(path))
    if not isinstance(payload, dict):
        raise Undecidable("the published file is not an object: " + str(path))
    return payload


def _age(stamp: object) -> float | None:
    """Seconds since an ISO stamp, or None when the stamp cannot be read."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _mode(path: Path) -> int | None:
    """The file's permission bits, or None when the file is gone."""
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return None


def _report_paths(report: dict) -> dict[str, str]:
    """The evidence paths the published coverage report names, keyed by path.

    The report is the plant's own reading: each outcome row it classified
    carries the evidence path that row bound. Only paths it actually bound are
    read; a row with no evidence is not a bound file.
    """
    outcomes = report.get("outcomes")
    if not isinstance(outcomes, dict):
        return {}
    items = outcomes.get("items")
    if not isinstance(items, list):
        return {}
    paths: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            continue
        path = evidence.get("path")
        if isinstance(path, str) and path:
            paths.setdefault(path, str(item.get("role") or "unknown"))
    return paths


def _record_paths(home: Path) -> dict[str, str]:
    """The evidence paths the published outcome records bind, keyed by path.

    The coverage report's window is the last 24 hours of recorded events, so
    the report alone does not see every binding the plant has made. The
    published record store does: each outcome record carries the evidence path
    and digest it bound at publication time. Records are read for that field
    only, and a record that is not a wall outcome, or that carries no evidence
    path, is skipped.
    """
    directory = home / RECORDS_DIR
    if not directory.is_dir():
        return {}
    paths: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") != OUTCOME_KIND:
            continue
        body = payload.get("payload")
        if not isinstance(body, dict):
            continue
        evidence = body.get("evidence")
        if not isinstance(evidence, dict):
            continue
        bound = evidence.get("path")
        if isinstance(bound, str) and bound:
            paths.setdefault(bound, str(body.get("role") or "unknown"))
    return paths


def _resolve(home: Path, bound: str) -> Path:
    """The file a bound evidence path names, as an absolute path."""
    target = Path(bound)
    if target.is_absolute():
        return target
    return home / target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True,
                        help="site home of the plant")
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE,
                        help="how old a published coverage report may be")
    args = parser.parse_args(argv)
    home = args.home.resolve()

    report_path = home / REPORT_NAME
    try:
        report = _read_json(report_path)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    age = _age(report.get("generated_at"))
    if age is None:
        print("UNKNOWN — the published coverage report carries no readable timestamp")
        return 3
    if age > args.max_age:
        print("UNKNOWN — the published coverage report is %d s old" % int(age))
        return 3

    report_paths = _report_paths(report)
    record_paths = _record_paths(home)
    paths = dict(record_paths)
    for path, role in report_paths.items():
        paths.setdefault(path, role)
    if not paths:
        print("UNKNOWN — no bound evidence path could be read")
        return 3

    writable: list[tuple[str, str, str]] = []
    readonly = 0
    missing = 0
    for bound, role in sorted(paths.items()):
        mode = _mode(_resolve(home, bound))
        if mode is None:
            missing += 1
            continue
        if mode & 0o222:
            writable.append((role, oct(mode)[-4:], bound))
        else:
            readonly += 1

    roles = Counter(role for role, _mode, _path in writable)
    print("report    : %s (%d s old)" % (report_path, int(age)))
    print("classified: %d evidence path(s) the coverage reader names" % len(report_paths))
    print("published : %d evidence path(s) the record store names" % len(record_paths))
    print("seen      : %d distinct bound evidence path(s)" % len(paths))
    print("read-only : %d" % readonly)
    print("writable  : %d" % len(writable))
    print("missing   : %d" % missing)
    if writable:
        print("roles     : %s"
              % ", ".join("%s=%d" % (role, count)
                          for role, count in sorted(roles.items(), key=lambda kv: (-kv[1], kv[0]))))
        for role, mode, bound in writable[:40]:
            print("            role=%s mode=%s %s" % (role, mode, bound))
        if len(writable) > 40:
            print("            ... and %d more" % (len(writable) - 40))

    if writable:
        print("verdict   : %d bound evidence file(s) are still writable — each is a "
              "row a later edit will drop permanently" % len(writable))
        return 2
    print("verdict   : every bound evidence file is read-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
