#!/usr/bin/env python3
"""Detector: a second reader must be able to classify every row from published fields.

The plant reads one binding fact twice. The **coverage reader**
(``initiative.collect``) re-reads each bound file and compares digests; a row
whose bytes moved becomes a coverage gap, and a role with a gap becomes the
role-level condition ``unknown:outcomes:<role>``. The **ledger reader**
(``discovery._ledger_evidence_binding``) classifies every violating row into
exactly one band -- ``owed``, ``recoverable``, ``disposed``, ``rebound``,
``cited``, ``versioned``, ``permanent`` -- and calls only ``owed`` repairable
debt.

Genome ruled (DM 85888) that the debt-banded reading (R4) is the way forward,
and asked that the ledger stay the *sole* band decider. R4's reader therefore
has to consume the band the ledger already decided. It may not re-derive it:
the gate costs about 13 s over the live home while the coverage producer cycles
every 60 s, so a second gate call per reading cannot keep up, and a
re-derivation is a second decider whatever it is called.

That makes one thing a precondition of R4, and it is a property of the plant's
**published** reading, not of any function in it: the published reading must
publish the band of every violating row, the published partition must agree
with itself, and every coverage gap row the ledger has classified must carry
its band. A reader that may only read published fields can then classify every
row -- including every row the coverage reader names -- without re-deriving
anything.

This check reads the published artifacts and nothing else:

* the plant's published ledger reading (``SITE/discovery/latest.json``, and
  the newest ``scan-*.json`` when the former is absent), read for its
  ``sense.ledger.evidence-binding`` observation;
* the plant's published coverage report (``SITE/checks/initiative.json``), read
  for the gap rows and the role set the coverage reader names.

It imports nothing from the plant package and calls no internal helper. That
is deliberate: on 2026-10-21 a measurement script of mine died silently when
``discovery._rebind_links`` changed its return arity, because it asserted on an
internal helper's signature. A check that reads published fields cannot die
that way, and the fields it asserts on are the contract a second reader
actually depends on.

The band vocabulary is not hardcoded here either: the reading publishes
``remediation_band_relations``, whose keys are the bands the plant defines and
whose values say what each band claims. A band outside that table is a name a
second reader has no definition for, so it is a finding rather than a band.

The check also prints the two readings side by side -- each coverage gap row
beside the band the ledger published for it, and the role set each reader
names -- so the disagreement between them stays legible after the ruling. The
role sets are reported, not judged: which roles hold debt is what R4 changes,
so a difference there is the ruling's subject, not a defect.

It also checks the reading's **discharge strength** and the **exposure over
the discharge dimension** -- the gate size if only a discharge that returned
the row's published bytes counted as clearing the row. The strength counts
must tally over the published rows, the discharge vocabulary must be closed
over the relations those rows use, and the exposure must be the arithmetic
those two imply. The vocabulary the reading publishes carries, per relation,
whether the relation is a claim about bytes at all and whether it returns the
published bytes, so this derivation reads published fields alone and does not
need the plant's source.

Exit status:
  0  the published partition agrees with itself, the band vocabulary is
     closed, every coverage gap row the ledger has classified carries a
     published band, and the published discharge exposure is the arithmetic
     the published rows imply
  2  the published reading publishes no band for its violating rows, the
     published partition contradicts itself, a band is outside the published
     vocabulary, a coverage gap row the ledger classified carries no
     published band, or the published discharge strength and exposure do not
     agree with the published rows: a second reader would have to re-derive
  3  the question cannot be decided: no published reading, an unreadable or
     stale one, no published coverage report, or one without the rows the
     property needs
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

SENSE_ID = "sense.ledger.evidence-binding"

#: The published fields the partition cross-check needs. Their absence is a
#: finding, not an inability to decide: a reading that publishes no band is a
#: reading no second reader can consume.
PARTITION_FIELDS = ("all_bad", "violations", "row_bands", "remediation_bands",
                    "remediation_band_sequences", "remediation_band_relations")

#: The published fields the discharge-strength cross-check needs. The exposure
#: over the discharge dimension is only meaningful if the reading publishes
#: the strength of its discharges, the vocabulary that says what each
#: relation claims, and the numbers the exposure was computed from.
DISCHARGE_FIELDS = ("discharge_strength", "discharge_relations",
                    "discharge_exposure", "discharge_exposure_definition")

#: The published detail lists whose rows must agree with the partition.
DETAIL_FIELDS = ("owed_detail", "linked_detail")

#: The published field naming the rows the ledger classified as outcome events.
#: A coverage gap row outside it was published after the reading was taken, or
#: the ledger could not decode it, so it has no band to carry.
CLASSIFIED_FIELDS = ("event_sequences",)

#: How old a published reading may be before the check cannot decide.
DEFAULT_MAX_AGE = 1800.0

#: How old a published coverage report may be before the check cannot decide.
DEFAULT_REPORT_MAX_AGE = 600.0


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
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _is_sequence(value: object) -> bool:
    """A tape sequence: an integer that is not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def published_reading(home: Path) -> tuple[dict, Path]:
    """The plant's published ledger reading, and where it was published."""
    latest = home / "discovery" / "latest.json"
    if latest.is_file():
        return _read_json(latest), latest
    directory = home / "discovery"
    samples = sorted(directory.glob("scan-*.json")) if directory.is_dir() else []
    if not samples:
        raise Undecidable("no published discovery reading under " + str(directory))
    return _read_json(samples[-1]), samples[-1]


def published_coverage_report(home: Path) -> tuple[dict, Path]:
    """The plant's published coverage report, and where it was published."""
    path = home / "checks" / "initiative.json"
    return _read_json(path), path


def ledger_entry(reading: dict) -> dict:
    """The evidence-binding observation inside a published reading."""
    observations = reading.get("observations")
    if not isinstance(observations, list):
        raise Undecidable("the published reading carries no observations")
    for item in observations:
        if isinstance(item, dict) and item.get("id") == SENSE_ID:
            return item
    raise Undecidable("the published reading carries no " + SENSE_ID)


def reading_stamp(entry: dict, reading: dict) -> float | None:
    """Seconds since the reading was taken, or None when it carries no stamp."""
    for value in (entry.get("read_at"), reading.get("created")):
        age = _age(value)
        if age is not None:
            return age
    return None


def report_stamp(report: dict) -> float | None:
    """Seconds since the coverage report was produced, or None."""
    for value in (report.get("generated_at"), report.get("producer_at")):
        age = _age(value)
        if age is not None:
            return age
    return None


def published_bands(entry: dict) -> dict:
    """The band of every row the published reading carries, by sequence."""
    bands: dict[int, str] = {}
    rows = entry.get("row_bands")
    if not isinstance(rows, list):
        return bands
    for row in rows:
        if not isinstance(row, dict):
            continue
        sequence = row.get("sequence")
        band = row.get("band")
        if _is_sequence(sequence) and isinstance(band, str) and band:
            bands[sequence] = band
    return bands


def published_vocabulary(entry: dict) -> set[str]:
    """The band names the published reading defines, and what each means."""
    relations = entry.get("remediation_band_relations")
    if not isinstance(relations, dict):
        return set()
    return {name for name in relations if isinstance(name, str)}


def discharge_vocabulary(entry: dict) -> dict:
    """The discharge relations the published reading defines, and their flags.

    The vocabulary says, per relation, whether the relation is a claim about
    bytes at all (``byte_relation``) and whether it returns the row's
    published bytes (``restores``). The reader needs both flags to re-derive
    the exposure, and it reads them from the published reading rather than
    from the plant's source, so the derivation stands on published fields
    alone.
    """
    relations = entry.get("discharge_relations")
    if not isinstance(relations, dict):
        return {}
    vocabulary = {}
    for name, claims in relations.items():
        if not isinstance(name, str) or not isinstance(claims, dict):
            continue
        vocabulary[name] = claims
    return vocabulary


def linked_relations(entry: dict) -> list[str]:
    """The discharge relation every published linked row carries."""
    relations: list[str] = []
    detail = entry.get("linked_detail")
    if not isinstance(detail, list):
        return relations
    for row in detail:
        if not isinstance(row, dict):
            continue
        relation = row.get("byte_relation")
        if isinstance(relation, str):
            relations.append(relation)
    return relations


def discharge_problems(entry: dict, bands: dict) -> list[str]:
    """Every way the published discharge strength disagrees with itself.

    The exposure over the discharge dimension is a number the reading's own
    verdict does not carry, so it has to be re-derivable from the published
    fields alone: the strength counts must tally over the published rows, the
    vocabulary must be closed over the relations those rows use, and the
    exposure must be the arithmetic those two imply.
    """
    problems: list[str] = []
    missing = [name for name in DISCHARGE_FIELDS if name not in entry]
    if missing:
        return ["the published reading publishes no " + ", ".join(missing) +
                ", so a second reader would have to re-derive the strength of "
                "the discharges that emptied the gate"]
    strength = entry["discharge_strength"]
    vocabulary = discharge_vocabulary(entry)
    exposure = entry["discharge_exposure"]
    definition = entry["discharge_exposure_definition"]
    if not isinstance(strength, dict):
        return ["the published discharge strength is not an object"]
    if not isinstance(exposure, dict):
        return ["the published discharge exposure is not an object"]
    if not isinstance(definition, str) or not definition:
        problems.append("the published discharge exposure carries no definition, "
                        "so a reader cannot tell the counterfactual from the gate")

    relations = linked_relations(entry)
    counted = {name: value for name, value in strength.items()
               if isinstance(name, str)}
    for name, value in counted.items():
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append("the published discharge strength says %s=%r, which "
                            "is not a count" % (name, value))
    tallied: dict[str, int] = {}
    for relation in relations:
        tallied[relation] = tallied.get(relation, 0) + 1
    for name, value in tallied.items():
        if name not in vocabulary:
            problems.append("the published rows carry the discharge relation %r, "
                            "which the published vocabulary does not define"
                            % (name,))
        if counted.get(name) != value:
            problems.append("the published discharge strength says %s=%r but the "
                            "published rows carry %d" % (name, counted.get(name), value))
    for name in counted:
        if name not in vocabulary:
            problems.append("the published discharge strength names %r, which the "
                            "published vocabulary does not define" % (name,))
        elif name not in tallied and counted[name]:
            # A relation the plant defines but never saw reads as a zero; a
            # relation with a count and no row behind it is a contradiction.
            problems.append("the published discharge strength says %s=%r but no "
                            "published row carries that relation"
                            % (name, counted[name]))

    gate_rows = len([1 for band in bands.values() if band == "owed"])
    byte_relations = {name for name, claims in vocabulary.items()
                      if claims.get("byte_relation") is True}
    restoring = {name for name, claims in vocabulary.items()
                 if claims.get("restores") is True}
    byte_discharges = sum(1 for relation in relations if relation in byte_relations)
    restored = sum(1 for relation in relations if relation in restoring)
    derived = {
        "gate_rows": gate_rows,
        "discharged_rows": len(relations),
        "byte_relation_discharges": byte_discharges,
        "restoring_discharges": restored,
        "non_restoring_discharges": byte_discharges - restored,
        "gate_if_only_restoring_counted": gate_rows + byte_discharges - restored,
    }
    for name, value in derived.items():
        published = exposure.get(name)
        if published != value:
            problems.append("the published discharge exposure says %s=%r but the "
                            "published fields imply %r" % (name, published, value))
    return problems


def producer_undetermined(entry: dict) -> str | None:
    """The reason the ledger reader determined no partition, or None.

    The producer returns early in two states and publishes no standing count
    with them: when the outcome store or the tape could not be read, and when
    there are no post-clause outcomes at all. Neither is a defect -- no
    partition was determined, so the question is undecidable rather than
    answered. A reading that publishes a standing count but no band is a
    different thing entirely: the producer determined the audit and published
    no band for it, which is the finding this check exists to name.
    """
    if "all_bad" in entry or "violations" in entry:
        return None
    sample = entry.get("sample")
    if isinstance(sample, str) and sample:
        return sample
    return "the published reading names no standing audit"


def partition_problems(entry: dict) -> list[str]:
    """Every way the published partition disagrees with itself."""
    problems: list[str] = []
    missing = [name for name in PARTITION_FIELDS if name not in entry]
    if missing:
        return ["the published reading publishes no " + ", ".join(missing) +
                ", so a second reader would have to re-derive every band"]
    rows = entry["row_bands"]
    table = entry["remediation_bands"]
    index = entry["remediation_band_sequences"]
    relations = entry["remediation_band_relations"]
    all_bad = entry["all_bad"]
    violations = entry["violations"]
    if not isinstance(rows, list):
        return ["the published per-row band partition is not a list"]
    if not isinstance(table, dict):
        return ["the published band table is not an object"]
    if not isinstance(index, dict):
        return ["the published band index is not an object"]
    if not isinstance(relations, dict):
        return ["the published band vocabulary is not an object"]
    if not isinstance(all_bad, int) or isinstance(all_bad, bool):
        return ["the published standing count is not a number"]
    if not isinstance(violations, list):
        return ["the published standing list is not a list"]

    bands = published_bands(entry)
    vocabulary = published_vocabulary(entry)
    if len(bands) != len(rows):
        problems.append("the published partition lists %d rows but carries a band "
                        "for %d of them" % (len(rows), len(bands)))
    if len(rows) != all_bad:
        problems.append("the published partition lists %d rows over a standing "
                        "audit of %d violating rows" % (len(rows), all_bad))
    if len(violations) != all_bad:
        problems.append("the published standing list names %d rows over a "
                        "standing audit of %d violating rows"
                        % (len(violations), all_bad))

    for name in sorted(vocabulary):
        if name not in table:
            problems.append("the published band table carries no count for %r, "
                            "which the vocabulary defines" % (name,))
    counted: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("band"), str):
            counted[row["band"]] = counted.get(row["band"], 0) + 1
    for name in sorted(set(counted) | set(table)):
        if counted.get(name, 0) != table.get(name):
            problems.append("the band table says %s=%r but the rows carry %d"
                            % (name, table.get(name), counted.get(name, 0)))
    for name in sorted(set(counted) - vocabulary):
        problems.append("the rows carry the band %r, which the published "
                        "vocabulary does not define" % (name,))

    indexed: dict[str, set[int]] = {}
    for name, sequences in index.items():
        if not isinstance(sequences, list):
            problems.append("the published band index for %r is not a list" % (name,))
            continue
        members = set()
        for member in sequences:
            if not _is_sequence(member):
                problems.append("the published band index for %r carries a "
                                "member that is not a sequence" % (name,))
                continue
            members.add(member)
        indexed[name] = members
    # Every published band name must be one the vocabulary defines, in the
    # table and in the index alike: a name no relation covers is a name a
    # second reader has no definition for.
    for name in sorted((set(table) | set(index)) - vocabulary):
        problems.append("the published bands name %r, which the published "
                        "vocabulary does not define" % (name,))
    by_band: dict[str, set[int]] = {}
    for sequence, band in bands.items():
        by_band.setdefault(band, set()).add(sequence)
    for name in sorted(set(indexed) | set(by_band)):
        if indexed.get(name, set()) != by_band.get(name, set()):
            problems.append("the published band index for %r does not carry the "
                            "rows that carry that band" % (name,))
    union: set[int] = set()
    duplicated: set[int] = set()
    for sequences in indexed.values():
        duplicated |= union & sequences
        union |= sequences
    if duplicated:
        problems.append("the published band index places %d row(s) in more than "
                        "one band" % (len(duplicated)))
    if union != set(bands):
        problems.append("the published band index covers %d rows and the "
                        "partition carries %d" % (len(union), len(bands)))

    owed_detail = entry.get("owed_detail")
    if not isinstance(owed_detail, list):
        problems.append("the published owed detail is not a list")
        owed_detail = []
    owed_rows: set[int] = set()
    for row in owed_detail:
        if not isinstance(row, dict):
            continue
        sequence = row.get("sequence")
        if not _is_sequence(sequence):
            problems.append("the published owed detail carries a row whose "
                            "sequence is not a sequence")
            continue
        owed_rows.add(sequence)
    owed_bands = {sequence for sequence, band in bands.items() if band == "owed"}
    if owed_rows != owed_bands:
        problems.append("the published owed detail names %d rows while the "
                        "partition bands %d rows as owed"
                        % (len(owed_rows), len(owed_bands)))
    for field in DETAIL_FIELDS:
        detail = entry.get(field)
        if not isinstance(detail, list):
            problems.append("the published %s is not a list" % (field,))
            continue
        for row in detail:
            if not isinstance(row, dict):
                continue
            sequence = row.get("sequence")
            band = row.get("band")
            if not _is_sequence(sequence):
                problems.append("the published %s carries a row whose sequence "
                                "is not a sequence" % (field,))
                continue
            if sequence not in bands:
                problems.append("the published %s carries a row the partition "
                                "does not cover: sequence %s" % (field, sequence))
                continue
            if field == "owed_detail" and band != "owed":
                problems.append("the published owed detail carries a row with "
                                "band %r" % (band,))
            if band != bands[sequence]:
                problems.append("the published %s carries a row the partition "
                                "bands %r" % (field, bands[sequence]))
    return problems


def coverage_gap_rows(report: dict) -> tuple[list[dict], list[str]]:
    """The gap rows the published coverage report names, and its own problems."""
    problems: list[str] = []
    outcomes = report.get("outcomes")
    if not isinstance(outcomes, dict):
        raise Undecidable("the published coverage report carries no outcomes")
    items = outcomes.get("items")
    if not isinstance(items, list):
        raise Undecidable("the published coverage report carries no outcome items")
    rows: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("state") == "OBSERVED":
            continue
        sequence = item.get("sequence")
        if _is_sequence(sequence):
            rows[sequence] = item

    listed: set[int] = set()
    roles = report.get("roles")
    if isinstance(roles, dict):
        for row in roles.values():
            if not isinstance(row, dict):
                continue
            gaps = row.get("outcome_gaps")
            if gaps is None:
                continue
            if not isinstance(gaps, list):
                problems.append("a published role row carries gap rows that are "
                                "not a list")
                continue
            for gap in gaps:
                if not isinstance(gap, dict):
                    continue
                sequence = gap.get("sequence")
                if _is_sequence(sequence):
                    listed.add(sequence)
    # The role rows carry every gap row the outcome items carry, and may carry
    # more: a publication the reader could not decode is a gap with no
    # outcome item behind it. The one direction is the invariant.
    missing = sorted(set(rows) - listed)
    if missing:
        problems.append("the published coverage report's role rows omit %d gap "
                        "row(s) the outcome items name: %s"
                        % (len(missing), ", ".join(str(s) for s in missing)))
    return [rows[sequence] for sequence in sorted(rows)], problems


def classified_sequences(entry: dict, problems: list[str]) -> set[int] | None:
    """The rows the ledger classified as outcome events, or None if unpublished."""
    for name in CLASSIFIED_FIELDS:
        value = entry.get(name)
        if isinstance(value, list):
            members = set()
            for member in value:
                if not _is_sequence(member):
                    problems.append("the published classified-row list carries a "
                                    "member that is not a sequence")
                    continue
                members.add(member)
            return members
    return None


def incomplete_sequences(entry: dict, problems: list[str]) -> set[int]:
    """The rows the ledger could not decode, so it holds no band for them."""
    value = entry.get("incomplete")
    if not isinstance(value, list):
        return set()
    members = set()
    for row in value:
        if not isinstance(row, dict):
            continue
        sequence = row.get("sequence")
        if not _is_sequence(sequence):
            problems.append("the published undecodable-row list carries a row "
                            "whose sequence is not a sequence")
            continue
        members.add(sequence)
    return members


def classified_by(sequence: int, classified: set[int] | None,
                  timestamp: object, ledger_read_at: object) -> bool:
    """Has the ledger classified this row, so it should carry a band?

    Two published facts answer it. The row's own sequence is in the ledger's
    classified set, or -- when the reading does not publish that set -- the
    row was published before the ledger reading, so the ledger had the chance
    to classify it and did not. The two stamps are parsed rather than compared
    as text: the feed stamps carry microseconds and the ledger's ``read_at``
    is truncated to seconds, so a text comparison would call a row published
    in the same second *before* a reading it followed.
    """
    if classified is not None:
        return sequence in classified
    published = _parse_stamp(timestamp)
    read = _parse_stamp(ledger_read_at)
    if published is None or read is None:
        return True
    return published <= read


def _parse_stamp(value: object) -> datetime | None:
    """An aware datetime from a published ISO stamp, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def debt_roles(entry: dict) -> set[str]:
    """The roles the published ledger reading names as holding debt."""
    roles: set[str] = set()
    detail = entry.get("owed_detail")
    if not isinstance(detail, list):
        return roles
    for row in detail:
        if isinstance(row, dict) and isinstance(row.get("role"), str):
            roles.add(row["role"])
    return roles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE,
                        help="seconds after which the published reading is stale")
    parser.add_argument("--report-max-age", type=float, default=DEFAULT_REPORT_MAX_AGE,
                        help="seconds after which the published coverage report is stale")
    args = parser.parse_args()
    home = args.home.resolve()

    try:
        reading, reading_path = published_reading(home)
        entry = ledger_entry(reading)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3

    age = reading_stamp(entry, reading)
    if age is None:
        print("UNKNOWN — the published reading carries no read timestamp")
        return 3
    if age > args.max_age:
        print("UNKNOWN — the published reading is stale (%d s old, limit %d s)"
              % (int(age), int(args.max_age)))
        return 3

    # The partition is checked first and on its own: a reading that publishes
    # no band is a finding whether or not the coverage report can be read.
    undetermined = producer_undetermined(entry)
    if undetermined is not None:
        print("reading   : %s" % reading_path)
        print("producer  : %s" % (reading.get("producer", "unknown")))
        print("read_at   : %d s old" % int(age))
        print("UNKNOWN — the ledger reader determined no standing audit: %s"
              % undetermined)
        return 3
    problems = partition_problems(entry)
    bands = published_bands(entry)
    problems.extend(discharge_problems(entry, bands))

    report = None
    report_path = None
    report_age = None
    report_note = None
    try:
        report, report_path = published_coverage_report(home)
    except Undecidable as exc:
        report_note = str(exc)
    if report is not None:
        report_age = report_stamp(report)
        if report_age is None:
            report_note = "the published coverage report carries no timestamp"
        elif report_age > args.report_max_age:
            report_note = ("the published coverage report is stale (%d s old, "
                           "limit %d s)" % (int(report_age), int(args.report_max_age)))

    gap_rows: list[dict] = []
    if report is not None and report_note is None:
        try:
            gap_rows, report_problems = coverage_gap_rows(report)
        except Undecidable as exc:
            report_note = str(exc)
        else:
            problems.extend(report_problems)

    classified = classified_sequences(entry, problems)
    ledger_read_at = entry.get("read_at")
    undecodable = incomplete_sequences(entry, problems)
    gap_roles: set[str] = set()
    unbanded: list[int] = []
    unseen: list[int] = []
    for row in gap_rows:
        sequence = row.get("sequence")
        if isinstance(row.get("role"), str):
            gap_roles.add(row["role"])
        if not _is_sequence(sequence) or sequence in bands:
            continue
        if sequence in undecodable:
            if classified is not None and sequence in classified:
                # The reading says the ledger both classified the row and could
                # not decode it. The two contradict, and the contradiction may
                # be hiding a row the ledger published no band for.
                problems.append("the published reading classifies sequence %s "
                                "and also reports it undecodable" % (sequence,))
                continue
            # The ledger could not decode the publication itself, so the row is
            # not a violating row and carries no band. Nothing to consume.
            continue
        if not classified_by(sequence, classified, row.get("timestamp"), ledger_read_at):
            # Published after the ledger reading, or not yet classified by it.
            # The ledger has not seen the row, so it has no band to publish.
            unseen.append(sequence)
            continue
        unbanded.append(sequence)
    if unbanded:
        problems.append("%d coverage gap row(s) the ledger classified carry no "
                        "published band: %s"
                        % (len(unbanded), ", ".join(str(s) for s in unbanded)))

    debt = debt_roles(entry)
    only_gap = sorted(gap_roles - debt)
    only_debt = sorted(debt - gap_roles)

    print("reading   : %s" % reading_path)
    print("producer  : %s" % (reading.get("producer", "unknown")))
    print("read_at   : %d s old" % int(age))
    if report_path is not None:
        print("report    : %s (%s)" % (report_path,
                                       "%d s old" % int(report_age) if report_age is not None
                                       else "no timestamp"))
    print("ledger    : all_bad=%s rows_with_band=%d" % (entry.get("all_bad"), len(bands)))
    print("coverage  : gap_rows=%d roles=%s" % (len(gap_rows), ",".join(sorted(gap_roles))))
    print("debt      : roles=%s" % ",".join(sorted(debt)))
    print("roles only in the coverage reading : %s" % (",".join(only_gap) or "none"))
    print("roles only in the ledger debt      : %s" % (",".join(only_debt) or "none"))
    for row in gap_rows:
        sequence = row.get("sequence")
        note = ""
        if _is_sequence(sequence) and sequence in unseen:
            note = " (published after the ledger reading; no band yet)"
        print("            seq=%s role=%s band=%s reason=%s%s"
              % (sequence, row.get("role"), bands.get(sequence, "NONE"),
                 row.get("reason", ""), note))
    if unseen:
        print("skew      : %d coverage gap row(s) were published after the "
              "ledger reading, so the ledger has not classified them yet: %s"
              % (len(unseen), ", ".join(str(s) for s in unseen)))

    if problems:
        for problem in problems:
            print("verdict   : INCONSISTENT — " + problem)
        return 2
    if report_note is not None:
        print("UNKNOWN — " + report_note)
        return 3
    print("verdict   : CONSISTENT — the published partition agrees with itself and "
          "every coverage gap row the ledger classified carries a published band")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
