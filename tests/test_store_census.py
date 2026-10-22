"""The store census measures the records the tape cannot show a reader."""
import json

from pathlib import Path

from mishe_tauftauf import records, store_census


def _record(records: Path, digest: str, kind: str) -> None:
    """Write a store record file under the digest that names it."""
    data = json.dumps({"version": 1, "kind": kind, "payload": {"role": "alice"}},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    (records / (digest + ".json")).write_bytes(data)


def test_a_record_head_names_its_kind():
    assert records.record_kind(b'{"kind":"wall-outcome","payload":{}}') == (
        "wall-outcome")
    assert records.record_kind(b'{"kind":"observation"}') == "observation"


def test_a_head_that_names_no_kind_is_not_guessed():
    assert records.record_kind(b'{"payload":{}}') is None
    assert records.record_kind(b'{"kind":"wall-out') is None


def test_the_census_counts_the_records_no_tape_entry_names(tmp_path):
    home = tmp_path / "site"
    records = home / "records"
    records.mkdir(parents=True)
    _record(records, "a" * 64, "wall-outcome")
    _record(records, "b" * 64, "wall-outcome")
    _record(records, "c" * 64, "observation")

    census = store_census.store_census(home, {"a" * 64})

    assert census["state"] == "verified"
    assert census["store_records"] == 3
    assert census["tape_named_records"] == 1
    assert census["records_without_tape_entry"] == 2
    # The subset that could hide an evidence binding: outcome records the
    # tape does not name.
    assert census["wall_outcome"] == 1
    assert census["kinds"] == {"observation": 1, "wall-outcome": 1}


def test_a_store_that_cannot_be_read_is_unknown(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True)

    census = store_census.store_census(home, set())

    assert census["state"] == "unknown"
    assert census["reason"]
    assert "wall_outcome" not in census
