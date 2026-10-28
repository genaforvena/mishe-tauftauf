"""The durable dispatch record: what a successor reads, and what it refuses."""
import json

import pytest

from mishe_tauftauf.core import (DispatchRecord, DispatchStore,
                                 DispatchStoreError, DispatchState)


def test_a_missing_record_reads_as_absent(tmp_path):
    store = DispatchStore(tmp_path / "dispatch")
    assert store.read("genome") is None


def test_a_record_survives_a_new_store_over_the_same_directory(tmp_path):
    store = DispatchStore(tmp_path / "dispatch")
    store.write(DispatchRecord(role="genome", key="wake-1",
                               state=DispatchState("unknown"), detail="effect unknown"))
    successor = DispatchStore(tmp_path / "dispatch")
    record = successor.read("genome")
    assert record.state.name == "unknown"
    assert record.detail == "effect unknown"


def test_the_record_of_one_role_is_not_erased_by_another_role(tmp_path):
    store = DispatchStore(tmp_path / "dispatch")
    store.write(DispatchRecord(role="genome", key="wake-1", state=DispatchState("completed")))
    store.write(DispatchRecord(role="health", key="wake-2", state=DispatchState("failed")))
    assert store.read("genome").key == "wake-1"
    assert store.read("health").key == "wake-2"


def test_a_malformed_record_is_an_error_not_an_absent_record(tmp_path):
    store = DispatchStore(tmp_path / "dispatch")
    path = store.path_for("genome")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DispatchStoreError):
        store.read("genome")


def test_a_record_with_an_unknown_state_is_refused(tmp_path):
    store = DispatchStore(tmp_path / "dispatch")
    store.path_for("genome").write_text(
        json.dumps({"role": "genome", "key": "wake-1", "state": "green"}), encoding="utf-8")
    with pytest.raises(DispatchStoreError):
        store.read("genome")


def test_a_role_name_that_could_escape_the_store_is_refused(tmp_path):
    store = DispatchStore(tmp_path / "dispatch")
    with pytest.raises(DispatchStoreError):
        store.path_for("../escape")
    with pytest.raises(DispatchStoreError):
        store.read("genome/../other")


def test_open_records_names_only_unresolved_effects(tmp_path):
    store = DispatchStore(tmp_path / "dispatch")
    store.write(DispatchRecord(role="genome", key="wake-1", state=DispatchState("started")))
    store.write(DispatchRecord(role="health", key="wake-2", state=DispatchState("completed")))
    store.write(DispatchRecord(role="witness", key="wake-3", state=DispatchState("unknown")))
    assert [record.role for record in store.open_records()] == ["genome", "witness"]


def test_the_record_is_written_atomically(tmp_path):
    store = DispatchStore(tmp_path / "dispatch")
    store.write(DispatchRecord(role="genome", key="wake-1", state=DispatchState("started")))
    # No temporary file survives the write, and the visible record is whole.
    assert sorted(path.name for path in (tmp_path / "dispatch").iterdir()) == ["genome.json"]
    assert store.read("genome").state.name == "started"


def test_clearing_a_record_keeps_the_key_history(tmp_path):
    """The key history is what stops a finished key from running again."""
    from mishe_tauftauf.core import DispatchStoreError
    store = DispatchStore(tmp_path / "dispatch")
    store.write(DispatchRecord(role="genome", key="k1",
                               state=DispatchState("completed")))
    assert store.used_keys("genome") == {"k1"}
    store.clear("genome")
    assert store.used_keys("genome") == {"k1"}, \
        "clearing the record erased the key history"


def test_clearing_an_open_record_is_refused(tmp_path):
    """An open record is not provably finished, so it is not cleared."""
    from mishe_tauftauf.core import DispatchStoreError
    store = DispatchStore(tmp_path / "dispatch")
    store.write(DispatchRecord(role="genome", key="k1", state=DispatchState("started")))
    with pytest.raises(DispatchStoreError):
        store.clear("genome")
    assert store.read("genome") is not None
