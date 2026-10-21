#!/usr/bin/env python3
"""Detector: the evidence-binding verdict must not certify a damaged ledger.

``sense.ledger.evidence-binding`` publishes one state word for the plant's
outcome-evidence ledger. Measured 2026-10-21 (research-methods, wake 77624),
the word is composed from a *live window* — the most recent outcome per role —
while the standing audit that actually describes the ledger sits beside it and
is never consulted:

    "drift" if latest_bad_roles else "unknown" if incomplete else "verified"

Two consequences, both measured on the plant's own records (3025 post-clause
outcomes):

* The live window self-heals. Every role publishes again, so the window empties
  while the standing loss stays: 140 class-B rows across 9 roles, with
  ``latest_bad=0``. The window's emptiness is evidence that nobody is violating
  *now*, not that the ledger is sound.
* The single coverage gap that keeps the verdict off ``verified`` binds no
  evidence path at all (tape sequence 66107 has no record reference, so no
  binding exists), and the plant's own writer already treats such gaps as
  non-blocking. Closing it — which nothing disputes — would move the verdict to
  ``verified`` over 211 violating rows.

So this check asks a property question about the *published reading*, not about
the code that produced it:

* **certifies** — the reading says ``verified`` while its own standing audit
  reports violating rows. A certificate over a damaged ledger is the defect.
* **resting on an irrelevant gap** — the reading says ``unknown`` while every
  coverage gap it lists binds no evidence path, so its indeterminacy is not
  about the subject it measures.
* **over its own gate** — the reading says ``verified`` or ``settled`` while it
  publishes rows it still owes. Both words claim a clean or fully accounted
  ledger, so neither may sit over an open gate. This shape only applies to a
  reading that publishes the gate counter, so a reading composed from the live
  window is not judged on a gate it never claimed.

It also prints the *exposure*: what the same published counters would say if
the irrelevant gaps were closed. The projection is derived from the reading's
own published fields, so it follows the producer that published it. A reading
composed from the gate exposes ``drift`` — closing a gap that binds nothing
does not move a verdict whose gate still holds owed rows — while a reading
composed from the live window exposes ``verified`` over standing losses,
which is the hazard this check exists to make visible.

The reading is the plant's own published sample, so the check measures what
the plant shows, not what the reviewed tree would show. A missing, stale or
incomplete reading is UNKNOWN, never OK.

Exit status:
  0  the published verdict is consistent with its own standing audit
  2  the published verdict certifies a damaged ledger, or rests on a gap that
     binds nothing
  3  the question cannot be decided: no published reading, a stale one, or one
     that does not carry the counters the property needs
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SENSE_ID = "sense.ledger.evidence-binding"

#: The counters the property needs. A reading without them cannot decide.
NEEDED = ("state", "all_bad")


def newest_sample(home: Path) -> Path | None:
    """The plant's newest published discovery sample, or None."""
    directory = home / "discovery"
    if not directory.is_dir():
        return None
    samples = sorted(directory.glob("scan-*.json"))
    return samples[-1] if samples else None


def read_at_of(entry: dict, sample: Path) -> datetime | None:
    """When the reading was taken, from the entry or the sample's own stamp."""
    for value in (entry.get("read_at"), sample.get("created")):
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def sense_entry(sample: dict) -> dict | None:
    """The evidence-binding observation inside a published sample."""
    for observation in sample.get("observations") or []:
        if isinstance(observation, dict) and observation.get("id") == SENSE_ID:
            return observation
    return None


def standing_rows(entry: dict) -> list:
    """The standing audit's rows, as published."""
    violations = entry.get("violations")
    return [row for row in violations if isinstance(row, str)] if isinstance(violations, list) else []


def per_role_standing(rows: list) -> dict:
    """How many standing rows each role holds, from the published rows."""
    counts: dict[str, int] = {}
    for row in rows:
        role = row.split("@", 1)[0]
        if role:
            counts[role] = counts.get(role, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def binding_gaps(entry: dict) -> list:
    """The coverage gaps that can still hide a bound evidence path."""
    gaps = entry.get("incomplete")
    if not isinstance(gaps, list):
        return []
    return [gap for gap in gaps
            if isinstance(gap, dict) and gap.get("binding", "unknown") != "none"]


def irrelevant_gaps(entry: dict) -> list:
    """The coverage gaps that bind nothing and so cannot hide a binding."""
    gaps = entry.get("incomplete")
    if not isinstance(gaps, list):
        return []
    return [gap for gap in gaps
            if isinstance(gap, dict) and gap.get("binding", "unknown") == "none"]


def excluded_count(entry: dict) -> int:
    """The standing loss count, under either name the readings use."""
    for key in ("permanent_loss", "count_excluded"):
        value = entry.get(key)
        if isinstance(value, int):
            return value
    sample = entry.get("sample")
    if isinstance(sample, str):
        found = re.search(r"(?:permanent_loss|count_excluded)=(\d+)", sample)
        if found:
            return int(found.group(1))
    return 0


def gate_rows(entry: dict) -> int:
    """The rows the reading publishes as still owed, or 0 when it publishes none.

    A reading that publishes ``owed_rows`` is composed from the gate: the
    verdict is ``drift`` while any row is owed. A reading that does not
    publish the counter is composed from the live window instead, and this
    returns 0 so the caller reads that older shape on its own terms.
    """
    value = entry.get("owed_rows")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def gate_contradiction(entry: dict) -> str | None:
    """Where the state's own claim contradicts the gate the reading publishes.

    ``verified`` claims a clean ledger and ``settled`` claims that every
    violation is accounted for, so neither may sit over a gate that still
    holds owed rows. A reading that publishes no gate counter cannot be
    checked this way, and no contradiction is claimed for it.
    """
    owed = entry.get("owed_rows")
    if not isinstance(owed, int) or isinstance(owed, bool) or owed <= 0:
        return None
    state = entry.get("state")
    if state in {"verified", "settled"}:
        return ("the verdict says '%s' while the reading publishes %d row(s) "
                "it still owes" % (state, owed))
    return None


def exposure_verdict(entry: dict) -> str:
    """What the same published counters would say if the irrelevant gaps closed.

    The projection is derived from the reading's own published fields, so it
    follows whichever producer published it rather than a hardcoded
    composition. A reading that publishes the gate is composed from that gate:
    closing a gap that binds nothing does not move the verdict while rows are
    still owed, so the exposure is ``drift``. A reading that publishes no gate
    counter is composed from the live window, where an empty window over
    standing losses does certify them — which is the hazard the check exists
    to make visible.
    """
    if "owed_rows" in entry:
        if gate_rows(entry) > 0:
            return "drift"
        if binding_gaps(entry):
            return "unknown"
        if excluded_count(entry) or standing_rows(entry):
            return "settled"
        return "verified"
    if entry.get("latest_bad"):
        return "drift"
    if binding_gaps(entry):
        return "unknown"
    return "verified"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--max-age", type=float, default=1800.0,
                        help="seconds after which a published reading is stale")
    args = parser.parse_args()
    home = args.home.resolve()

    sample = newest_sample(home)
    if sample is None:
        print("UNKNOWN — no published discovery sample under " + str(home / "discovery"))
        return 3
    try:
        payload = json.loads(sample.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("UNKNOWN — the newest published sample is unreadable: " + str(exc))
        return 3
    if not isinstance(payload, dict):
        print("UNKNOWN — the newest published sample is not a reading")
        return 3
    entry = sense_entry(payload)
    if entry is None:
        print("UNKNOWN — the newest published sample carries no " + SENSE_ID)
        return 3
    missing = [key for key in NEEDED if key not in entry]
    if missing:
        print("UNKNOWN — the published reading carries no " + ", ".join(missing))
        return 3

    state = entry.get("state")
    all_bad = entry.get("all_bad")
    read_at = read_at_of(entry, payload)
    if read_at is None:
        print("UNKNOWN — the published reading carries no read timestamp")
        return 3
    age = (datetime.now(timezone.utc) - read_at).total_seconds()
    if age > args.max_age:
        print("UNKNOWN — the published reading is stale (%d s old, limit %d s)"
              % (int(age), int(args.max_age)))
        return 3

    excluded = excluded_count(entry)
    rows = standing_rows(entry)
    roles = per_role_standing(rows)
    binding = binding_gaps(entry)
    irrelevant = irrelevant_gaps(entry)
    latest_bad = entry.get("latest_bad") if isinstance(entry.get("latest_bad"), int) else 0

    print("reading   :", sample.name)
    print("producer  :", payload.get("producer", "unknown"))
    print("read_at   :", read_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "(age %ds)" % int(age))
    print("state     :", state)
    print("standing  : all_bad=%s permanent_loss=%s latest_bad=%s rows=%d"
          % (all_bad, excluded, latest_bad, len(rows)))
    if "owed_rows" in entry:
        print("gate      : owed_rows=%d" % gate_rows(entry))
    if roles:
        print("per role  : " + ", ".join("%s=%d" % item for item in roles.items()))
    print("gaps      : %d binding-relevant, %d binding nothing"
          % (len(binding), len(irrelevant)))
    for gap in binding + irrelevant:
        if isinstance(gap, dict):
            print("            seq=%s binding=%s %s"
                  % (gap.get("sequence"), gap.get("binding"), gap.get("reason", "")))
    print("exposure  : closing the binding-irrelevant gaps would make the same "
          "counters read '" + exposure_verdict(entry) + "'")

    problems = []
    if state == "verified" and (all_bad or excluded):
        problems.append(
            "the verdict says 'verified' while its own standing audit reports "
            "%s violating row(s) (%s permanent loss)" % (all_bad, excluded))
    if state == "unknown" and not binding:
        problems.append(
            "the verdict says 'unknown' while every coverage gap it lists binds no "
            "evidence path, so its indeterminacy is not about the ledger")
    contradiction = gate_contradiction(entry)
    if contradiction:
        problems.append(contradiction)
    if problems:
        for problem in problems:
            print("verdict   : INCONSISTENT — " + problem)
        return 2
    print("verdict   : CONSISTENT — the published verdict agrees with its own "
          "standing audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
