"""The second-reader detector must decide from published fields alone.

``checks/ledger-band-second-reader.py`` answers whether a reader that may
only read the plant's *published* artifacts can classify every violating row
by band, without re-deriving one. That is the precondition of R4, the
debt-banded reading genome ruled (DM 85888): the ledger stays the sole band
decider, so the band must arrive as published data rather than as a second
gate call per reading.

These tests pin the detector's decisions on a synthetic site home, so the
readings are fixed by the fixture rather than by the machine and the same
assertions hold in CI and on the plant:

* a consistent published partition with every coverage gap row banded reads
  CONSISTENT;
* a gap row the published partition does not cover reads INCONSISTENT and
  names the row;
* a band table that disagrees with the rows, a band index that disagrees
  with the rows, a row in two bands, an owed detail row that is not ``owed``,
  a detail row the partition does not cover, a standing list whose length
  disagrees with the standing count, and a band outside the published
  vocabulary each read INCONSISTENT and say which relation broke;
* a published reading with no band partition at all reads INCONSISTENT -- the
  condition the plant is in while its running producer predates the
  publication (measured 2026-10-21: the live producer is 63 commits behind
  HEAD and publishes no band fields);
* a coverage gap row published after the ledger reading is *skew*, not a
  finding: the ledger has not classified it yet, so it carries no band and
  that is not a defect;
* a malformed published field reads INCONSISTENT naming the field, and never
  a traceback;
* a missing, unreadable or stale reading, and a missing or stale coverage
  report, read UNKNOWN, never green.

The detector reads published artifacts and imports nothing from the plant
package, so a change to an internal helper's signature cannot make it fail.
That is deliberate: on 2026-10-21 a measurement script of discover's died
silently when ``discovery._rebind_links`` changed its return arity.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "ledger-band-second-reader.py"

#: The bands the plant defines, as the reading publishes them.
BANDS = ("owed", "recoverable", "disposed", "rebound", "cited", "versioned",
         "permanent")


def run_check(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(home), *extra],
                          capture_output=True, text=True)


def stamp(offset_seconds: float = 0.0) -> str:
    """An ISO stamp, ``offset_seconds`` in the past (negative is in the future)."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_reading(home: Path, entry: dict, *, age: float = 0.0,
                  read_at: str | None = None) -> None:
    """Publish a discovery sample carrying one evidence-binding reading."""
    directory = home / "discovery"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"created": stamp(age),
               "producer": "test-producer@0",
               "observations": [dict(entry, id="sense.ledger.evidence-binding",
                                     read_at=read_at or stamp(age))]}
    (directory / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def write_report(home: Path, *, gap_sequences: list[int] = (),
                 role: str = "health", age: float = 0.0,
                 role_rows: list[int] | None = None,
                 extra_role_gaps: list[int] = (),
                 gap_timestamps: dict[int, str] | None = None) -> None:
    """Publish a coverage report naming the given gap rows.

    ``role_rows`` overrides the sequences the role rows carry, and
    ``extra_role_gaps`` adds role rows the outcome items do not name -- the
    shape a publication the reader could not decode produces.
    ``gap_timestamps`` overrides a gap row's own publication time, which is
    how a row published after the ledger reading is expressed.
    """
    directory = home / "checks"
    directory.mkdir(parents=True, exist_ok=True)
    stamps = gap_timestamps or {}

    def gap_item(sequence: int) -> dict:
        return {"sequence": sequence, "role": role, "state": "UNKNOWN",
                "reason": "outcome evidence empty or changed",
                "timestamp": stamps.get(sequence, stamp(3600))}

    items = [gap_item(sequence) for sequence in gap_sequences]
    items.append({"sequence": 999, "role": role, "state": "OBSERVED", "reason": ""})
    rows = role_rows if role_rows is not None else list(gap_sequences)
    gaps = [gap_item(sequence) for sequence in list(rows) + list(extra_role_gaps)]
    (directory / "initiative.json").write_text(json.dumps({
        "generated_at": stamp(age),
        "outcomes": {"state": "PARTIAL", "items": items, "gaps": []},
        "roles": {role: {"outcome_coverage": "PARTIAL", "outcome_gaps": gaps}},
    }), encoding="utf-8")


def partition(rows: list[tuple[int, str]], *, table: dict | None = None,
              index: dict | None = None, all_bad: int | None = None,
              violations: list[str] | None = None,
              owed: list[dict] | None = None, linked: list[dict] | None = None,
              vocabulary: dict | None = None,
              classified: list[int] | None = None,
              discharge_strength: dict | None = None,
              discharge_relations: dict | None = None,
              discharge_exposure: dict | None = None) -> dict:
    """A published partition over the given (sequence, band) rows."""
    band_rows = [{"sequence": sequence, "role": "health", "band": band,
                  "classes": "B", "evidence": "artifacts/a.md"}
                 for sequence, band in rows]
    counted: dict[str, int] = {name: 0 for name in BANDS}
    for _sequence, band in rows:
        counted[band] = counted.get(band, 0) + 1
    linked_rows = linked if linked is not None else []
    entry = {
        "state": "drift",
        "all_bad": len(rows) if all_bad is None else all_bad,
        "bound": len(rows),
        "violations": (["health@2026-10-21T00:00:00Z:artifacts/a.md(B)"] * len(rows)
                       if violations is None else violations),
        "row_bands": band_rows,
        "remediation_bands": table if table is not None else counted,
        "remediation_band_sequences": index if index is not None else {
            band: sorted(s for s, b in rows if b == band) for band in counted},
        "remediation_band_relations": vocabulary if vocabulary is not None else {
            name: "the relation %s decides" % name for name in BANDS},
        "owed_detail": owed if owed is not None else [
            {"sequence": s, "role": "health", "band": "owed"} for s, b in rows if b == "owed"],
        "linked_detail": linked_rows,
        "event_sequences": classified if classified is not None else [
            s for s, _b in rows],
        "read_at": stamp(),
    }
    entry.update(discharge_fields(rows, linked_rows, discharge_strength,
                                  discharge_relations, discharge_exposure))
    return entry


#: The discharge relations the published reading defines, with the flags the
#: exposure is derived from: whether the relation is a claim about bytes at
#: all, and whether it returns the row's published bytes.
DISCHARGE_VOCABULARY = {
    "restored": {"claims": "the published bytes came back",
                 "byte_relation": True, "restores": True},
    "copy-of-current": {"claims": "the work was carried forward",
                        "byte_relation": True, "restores": False},
    "unrelated": {"claims": "neither the published bytes nor the current ones",
                  "byte_relation": True, "restores": False},
    "unknown": {"claims": "the relation cannot be decided",
                "byte_relation": True, "restores": False},
    "not-a-byte-relation": {"claims": "no claim about the published bytes",
                            "byte_relation": False, "restores": False},
}

#: The scopes the exposure is published over, and the published field each
#: scope's population is counted over.
EXPOSURE_SCOPES = {
    "all_discharged_rows": "discharged_rows",
    "byte_relation_discharges": "byte_relation_discharges",
}


def discharge_fields(rows: list[tuple[int, str]], linked_rows: list[dict],
                     strength: dict | None = None,
                     relations: dict | None = None,
                     exposure: dict | None = None) -> dict:
    """The published discharge strength and exposure, consistent with the rows.

    The counts tally over the published rows and the exposure is the
    arithmetic they imply, so a reading built here is one a second reader can
    check without re-deriving anything. The scope is published as data: each
    scope names the field its population is counted over and carries its own
    definition, so the figure and the definition beside it cannot be read two
    ways.
    """
    vocabulary = relations if relations is not None else DISCHARGE_VOCABULARY
    counted = {name: 0 for name in vocabulary}
    for row in linked_rows:
        relation = row.get("byte_relation")
        if isinstance(relation, str):
            counted[relation] = counted.get(relation, 0) + 1
    if strength is not None:
        counted = strength
    gate_rows = len([1 for _s, band in rows if band == "owed"])
    byte_relations = {name for name, claims in vocabulary.items()
                      if claims.get("byte_relation") is True}
    restoring = {name for name, claims in vocabulary.items()
                 if claims.get("restores") is True}
    byte_discharges = sum(counted.get(name, 0) for name in byte_relations)
    restored = sum(counted.get(name, 0) for name in restoring)
    populations = {"discharged_rows": sum(counted.values()),
                   "byte_relation_discharges": byte_discharges}
    scopes: dict[str, dict] = {}
    for name, field in EXPOSURE_SCOPES.items():
        population = populations[field]
        non_restoring = max(0, population - restored)
        scopes[name] = {
            "population_field": field,
            "population": population,
            "non_restoring_discharges": non_restoring,
            "gate_if_only_restoring_counted": gate_rows + non_restoring,
            "definition": ("the gate size if only a discharge that returned the "
                           "row's published bytes counted as clearing the row, "
                           "over " + name),
        }
    named = "all_discharged_rows"
    published = {
        "gate_rows": gate_rows,
        "discharged_rows": populations["discharged_rows"],
        "byte_relation_discharges": byte_discharges,
        "restoring_discharges": restored,
        "exposure_scope": named,
        "non_restoring_discharges": scopes[named]["non_restoring_discharges"],
        "gate_if_only_restoring_counted": scopes[named]["gate_if_only_restoring_counted"],
        "exposure_scopes": scopes,
    }
    return {
        "discharge_strength": counted,
        "discharge_relations": vocabulary,
        "discharge_exposure": published if exposure is None else exposure,
        "discharge_exposure_definition": scopes[named]["definition"],
        "audited_population": {
            "source": "the current chat tape (chat.log)",
            "kind": "post-clause wall-outcome publications the tape names",
            "size": len(rows),
            "window_scoped": True,
            "clause_time": "2026-10-06T16:22:11+00:00",
        },
        "unaudited_population": {
            "source": "the outcome store (records/)",
            "basis": "store records of kind wall-outcome that no entry of the "
                     "current tape names",
            "size": 7,
            "records_without_tape_entry": 19,
            "store_records": 64,
            "tape_named_records": 45,
            "state": "verified",
            "measured_at": stamp(),
        },
    }


def test_a_consistent_partition_with_banded_gap_rows_reads_consistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed"), (2, "recoverable"), (3, "versioned")]))
    write_report(home, gap_sequences=[1, 2, 3], role="health")

    result = run_check(home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONSISTENT" in result.stdout
    assert "INCONSISTENT" not in result.stdout


def test_a_gap_row_the_partition_does_not_cover_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")], classified=[1, 42]))
    write_report(home, gap_sequences=[1, 42], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "INCONSISTENT" in result.stdout
    assert "42" in result.stdout


def test_a_band_table_that_disagrees_with_the_rows_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed"), (2, "owed")],
                                   table={"owed": 1}))
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "band table says owed=1" in result.stdout


def test_a_band_index_that_disagrees_with_the_rows_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed"), (2, "owed")],
                                   index={"owed": [1]}))
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "band index for 'owed'" in result.stdout


def test_a_row_in_two_bands_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")],
                                   index={"owed": [1], "cited": [1]}))
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "more than one band" in result.stdout


def test_an_owed_detail_row_that_is_not_owed_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")],
                                   owed=[{"sequence": 1, "role": "health", "band": "cited"}]))
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "owed detail carries a row with band" in result.stdout


def test_a_detail_row_the_partition_does_not_cover_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "recoverable")],
                                   linked=[{"sequence": 2, "role": "health",
                                            "band": "cited", "kind": "digest-cited"}]))
    write_report(home, gap_sequences=[], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "carries a row the partition does not cover" in result.stdout


def test_a_standing_list_that_disagrees_with_the_count_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")],
                                   violations=["health@2026-10-21T00:00:00Z:artifacts/a.md(B)",
                                               "health@2026-10-21T00:00:01Z:artifacts/b.md(B)"]))
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "standing list names 2 rows" in result.stdout


def test_a_band_outside_the_published_vocabulary_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "banana")],
                                   table={"banana": 1},
                                   index={"banana": [1]}))
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "vocabulary does not define" in result.stdout


def test_a_published_reading_without_a_band_partition_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, {"state": "unknown", "all_bad": 213, "violations": []})
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "publishes no" in result.stdout
    assert "re-derive" in result.stdout


def test_a_missing_band_partition_is_inconsistent_even_without_a_coverage_report(tmp_path):
    """A known failed condition is not undecidable because a second artifact is absent."""
    home = tmp_path / "home"
    write_reading(home, {"state": "unknown", "all_bad": 1,
                          "violations": ["health@2026-10-21T00:00:00Z:artifacts/a.md(B)"]})

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "publishes no" in result.stdout


def test_a_gap_row_published_after_the_ledger_reading_is_skew_not_a_finding(tmp_path):
    """The ledger has not classified the row yet, so it carries no band and that is not a defect."""
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")], classified=[1]))
    write_report(home, gap_sequences=[1, 77], role="health",
                 gap_timestamps={77: stamp(-60)})

    result = run_check(home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONSISTENT" in result.stdout
    assert "published after the ledger reading" in result.stdout
    assert "77" in result.stdout


def test_a_gap_row_the_ledger_could_not_decode_is_not_a_finding(tmp_path):
    """A publication the reader could not decode is not a violating row, so it carries no band."""
    home = tmp_path / "home"
    entry = partition([(1, "owed")], classified=[1])
    entry["incomplete"] = [{"sequence": 77, "reason": "malformed header",
                            "binding": "unknown"}]
    write_reading(home, entry)
    write_report(home, gap_sequences=[1, 77], role="health")

    result = run_check(home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONSISTENT" in result.stdout


def test_a_role_row_the_outcome_items_omit_is_inconsistent(tmp_path):
    """The role rows must carry every gap row the outcome items name."""
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")]))
    write_report(home, gap_sequences=[1], role="health", role_rows=[])

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "role rows omit" in result.stdout


def test_a_role_row_the_outcome_items_do_not_name_is_not_a_finding(tmp_path):
    """A publication the reader could not decode is a gap with no outcome item behind it."""
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")]))
    write_report(home, gap_sequences=[1], role="health", extra_role_gaps=[55])

    result = run_check(home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONSISTENT" in result.stdout


def test_a_malformed_partition_field_is_inconsistent_and_never_a_traceback(tmp_path):
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    entry["row_bands"] = 1
    write_reading(home, entry)
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "INCONSISTENT" in result.stdout
    assert "Traceback" not in result.stderr


def test_a_malformed_detail_field_is_inconsistent_and_never_a_traceback(tmp_path):
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    entry["owed_detail"] = 3
    write_reading(home, entry)
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "INCONSISTENT" in result.stdout
    assert "Traceback" not in result.stderr


def test_the_two_role_sets_are_reported_without_judging_them(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")],
                                   owed=[{"sequence": 1, "role": "genome", "band": "owed"}]))
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "roles only in the coverage reading : health" in result.stdout
    assert "roles only in the ledger debt      : genome" in result.stdout


def test_a_detail_row_with_a_malformed_sequence_is_inconsistent_not_a_traceback(tmp_path):
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    entry["owed_detail"] = [{"sequence": []}]
    write_reading(home, entry)
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_a_role_row_with_malformed_gap_rows_is_inconsistent_not_a_traceback(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")]))
    write_report(home, gap_sequences=[1], role="health")
    report_path = home / "checks" / "initiative.json"
    report = json.loads(report_path.read_text())
    report["roles"]["health"]["outcome_gaps"] = 1
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_a_reading_the_producer_could_not_compute_is_unknown(tmp_path):
    """The producer's own unavailability shape determines no partition, so the question is undecidable."""
    home = tmp_path / "home"
    write_reading(home, {"state": "unknown", "sample": "chat tape unavailable",
                          "kind": "read", "coverage_incomplete": 0, "incomplete": []})
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout
    assert "determined no standing audit" in result.stdout


def test_a_reading_with_no_post_clause_outcomes_is_unknown(tmp_path):
    home = tmp_path / "home"
    write_reading(home, {"state": "unknown", "sample": "no post-clause outcomes",
                          "kind": "read", "coverage_incomplete": 0, "incomplete": []})
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_row_in_the_same_second_as_the_reading_is_compared_as_a_time(tmp_path):
    """The ledger stamps to the second and the feed to the microsecond, so text comparison would invert them."""
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    del entry["event_sequences"]
    # The ledger's read_at is truncated to the second; the feed's stamp carries
    # the microsecond, so the row is later within the very second the reading
    # names.
    reading_second = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_reading(home, entry, read_at=reading_second)
    write_report(home, gap_sequences=[1, 77], role="health",
                 gap_timestamps={77: reading_second[:-1] + ".900000Z"})

    result = run_check(home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "published after the ledger reading" in result.stdout


def test_a_row_both_classified_and_undecodable_is_inconsistent(tmp_path):
    """The two published sets contradict, and the contradiction can hide an unbanded row."""
    home = tmp_path / "home"
    entry = partition([(1, "owed")], classified=[1, 77])
    entry["incomplete"] = [{"sequence": 77, "reason": "malformed header",
                            "binding": "unknown"}]
    write_reading(home, entry)
    write_report(home, gap_sequences=[1, 77], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "classifies sequence 77" in result.stdout


def test_a_malformed_band_index_member_is_inconsistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")], index={"owed": [1, []]}))
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "not a sequence" in result.stdout


def test_an_undefined_zero_count_band_is_inconsistent(tmp_path):
    """A band name the vocabulary does not define is a name a reader has no definition for."""
    home = tmp_path / "home"
    table = {name: 0 for name in BANDS}
    table["banana"] = 0
    write_reading(home, partition([], table=table))
    write_report(home, gap_sequences=[], role="health")

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "vocabulary does not define" in result.stdout


def test_a_missing_reading_is_unknown(tmp_path):
    home = tmp_path / "home"
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_an_unreadable_reading_is_unknown(tmp_path):
    home = tmp_path / "home"
    (home / "discovery").mkdir(parents=True)
    (home / "discovery" / "latest.json").write_bytes(b"\x00\x01not json")

    result = run_check(home)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_stale_reading_is_unknown(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")]), age=7200)
    write_report(home, gap_sequences=[1], role="health")

    result = run_check(home)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "stale" in result.stdout


def test_a_missing_coverage_report_is_unknown(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")]))

    result = run_check(home)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_stale_coverage_report_is_unknown(tmp_path):
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed")]))
    write_report(home, gap_sequences=[1], role="health", age=3600)

    result = run_check(home)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "stale" in result.stdout


def test_a_malformed_reading_is_unknown(tmp_path):
    home = tmp_path / "home"
    (home / "discovery").mkdir(parents=True)
    (home / "discovery" / "latest.json").write_text("{not json", encoding="utf-8")

    result = run_check(home)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_the_check_reads_no_plant_module():
    """The detector asserts on published fields, not on an internal signature."""
    source = CHECK.read_text(encoding="utf-8")
    assert "mishe_tauftauf" not in source
    assert "import discovery" not in source


def _linked(sequence: int, relation: str) -> dict:
    """A published cleared row carrying the given discharge relation."""
    return {"sequence": sequence, "kind": "remedy-name", "band": "rebound",
            "successor": sequence + 1, "role": "health",
            "byte_relation": relation,
            "successor_evidence": "artifacts/b.md"}


def test_a_discharge_strength_that_agrees_with_the_rows_reads_consistent(tmp_path):
    """The published strength tallies over the published rows."""
    home = tmp_path / "home"
    write_reading(home, partition([(1, "owed"), (2, "rebound")],
                                   linked=[_linked(2, "copy-of-current")]))

    result = run_check(home)

    assert "INCONSISTENT" not in result.stdout


def test_a_discharge_strength_that_does_not_tally_is_inconsistent(tmp_path):
    """A count the published rows do not support is a finding."""
    home = tmp_path / "home"
    entry = partition([(1, "owed"), (2, "rebound")],
                      linked=[_linked(2, "copy-of-current")])
    entry["discharge_strength"] = dict(entry["discharge_strength"])
    entry["discharge_strength"]["copy-of-current"] = 5
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "copy-of-current" in result.stdout


def test_a_discharge_exposure_that_disagrees_with_the_rows_is_inconsistent(tmp_path):
    """The exposure must be the arithmetic the published rows imply.

    An exposure a second reader cannot re-derive is a certifying number only
    its author can recompute, so the check recomputes it from the published
    strength, the published vocabulary and the published partition.
    """
    home = tmp_path / "home"
    entry = partition([(1, "owed"), (2, "rebound")],
                      linked=[_linked(2, "copy-of-current")])
    entry["discharge_exposure"] = dict(entry["discharge_exposure"])
    entry["discharge_exposure"]["gate_if_only_restoring_counted"] = 99
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "gate_if_only_restoring_counted" in result.stdout


def test_a_reading_with_no_discharge_strength_is_inconsistent(tmp_path):
    """A reading that publishes no discharge strength cannot be checked.

    The exposure over the discharge dimension is only meaningful beside the
    strength it was computed from, so a reading without one leaves a second
    reader to re-derive it.
    """
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    del entry["discharge_strength"]
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "discharge_strength" in result.stdout


def test_a_discharge_relation_outside_the_published_vocabulary_is_inconsistent(tmp_path):
    """A relation the vocabulary does not define is a name with no claim."""
    home = tmp_path / "home"
    entry = partition([(1, "owed"), (2, "rebound")],
                      linked=[_linked(2, "carried-forward")])
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "does not define" in result.stdout


def test_a_discharge_exposure_with_no_definition_is_inconsistent(tmp_path):
    """A counterfactual with no definition cannot be told from the gate."""
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    entry["discharge_exposure_definition"] = ""
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "definition" in result.stdout


def test_a_scope_figure_from_another_scope_is_inconsistent(tmp_path):
    """A figure that belongs to another scope cannot stand under this one.

    The scope says which discharges the counterfactual counts, and it is
    published as data, so a second reader re-derives the figure from the
    scope's own population instead of trusting the number beside it.
    """
    home = tmp_path / "home"
    entry = partition([(1, "owed"), (2, "rebound"), (3, "cited")],
                      linked=[_linked(2, "copy-of-current"),
                              _linked(3, "not-a-byte-relation")])
    scopes = entry["discharge_exposure"]["exposure_scopes"]
    entry["discharge_exposure"]["gate_if_only_restoring_counted"] = (
        scopes["byte_relation_discharges"]["gate_if_only_restoring_counted"])
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "all_discharged_rows" in result.stdout


def test_a_definition_from_another_scope_is_inconsistent(tmp_path):
    """The definition beside a figure must be that figure's own scope.

    A definition that states one counterfactual and a number that counts
    another is the defect the scope makes checkable: the reader compares the
    published definition with the definition the named scope publishes.
    """
    home = tmp_path / "home"
    entry = partition([(1, "owed"), (2, "rebound")],
                      linked=[_linked(2, "copy-of-current")])
    scopes = entry["discharge_exposure"]["exposure_scopes"]
    entry["discharge_exposure_definition"] = scopes["byte_relation_discharges"]["definition"]
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "another scope" in result.stdout


def test_a_reading_with_no_exposure_scopes_is_inconsistent(tmp_path):
    """A figure with no scope cannot be checked against one."""
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    del entry["discharge_exposure"]["exposure_scopes"]
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "scope" in result.stdout


def test_a_scope_population_that_disagrees_with_the_published_field_is_inconsistent(tmp_path):
    """A scope's population must be the published field it names."""
    home = tmp_path / "home"
    entry = partition([(1, "owed"), (2, "rebound")],
                      linked=[_linked(2, "copy-of-current")])
    scopes = entry["discharge_exposure"]["exposure_scopes"]
    scopes["byte_relation_discharges"]["population"] = 99
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "population" in result.stdout


def test_a_reading_with_no_populations_is_inconsistent(tmp_path):
    """A count with no population beside it cannot be read as a whole."""
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    del entry["audited_population"]
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "audited population" in result.stdout


def test_an_audited_population_that_disagrees_with_the_count_is_inconsistent(tmp_path):
    """The published population's size must be the count the reading publishes."""
    home = tmp_path / "home"
    entry = partition([(1, "owed"), (2, "rebound")])
    entry["audited_population"] = dict(entry["audited_population"])
    entry["audited_population"]["size"] = 9
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "audited population" in result.stdout


def test_an_unaudited_population_larger_than_the_store_complement_is_inconsistent(tmp_path):
    """The unaudited population cannot exceed the records it is counted over."""
    home = tmp_path / "home"
    entry = partition([(1, "owed")])
    entry["unaudited_population"] = dict(entry["unaudited_population"])
    entry["unaudited_population"]["size"] = 999
    write_reading(home, entry)

    result = run_check(home)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "unaudited population" in result.stdout
