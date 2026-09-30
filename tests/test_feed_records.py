import json
from dataclasses import replace
import pytest
from mishe_tauftauf.feed import Feed, FeedEntry, parse_feed


def test_record_is_readable_and_replays(tmp_path):
    from mishe_tauftauf.records import payload
    entry = Feed(tmp_path).append_record("witness", "The dependency check found a missing producer.", {"producer": "garden"})
    assert '{' not in entry.body
    assert payload(entry) == {"producer": "garden"}
    assert payload(Feed(tmp_path).entries()[0]) == payload(entry)
    assert payload(parse_feed(Feed(tmp_path).read_bytes(), home=tmp_path)[0]) == payload(entry)


def test_legacy_payload_without_home():
    from mishe_tauftauf.records import payload
    assert payload(FeedEntry(1, "time", "seed", '[task-event] ready\n{"reason":"checked"}\nChecked.')) == {"reason": "checked"}


def test_feed_reader_does_not_hide_a_missing_committed_record(tmp_path):
    Feed(tmp_path).append_record('witness', 'The checked producer result is available for review.', {'checked': True})
    next((tmp_path / 'records').glob('*.json')).unlink()
    with pytest.raises(ValueError, match='record'):
        Feed(tmp_path).entries()


def test_ordinary_reference_cannot_publish_missing_evidence(tmp_path):
    from mishe_tauftauf.records import reference
    with pytest.raises(ValueError, match='record'):
        Feed(tmp_path).append('witness', 'The evidence supports this result.\n' + reference('a'*64))
    assert Feed(tmp_path).tail_sequence() == 0


def test_malformed_record_marker_cannot_bypass_admission(tmp_path):
    with pytest.raises(ValueError, match='record'):
        Feed(tmp_path).append('witness', 'Evidence is available for this checked result.\n[record]\trecords/missing.json')
    assert Feed(tmp_path).tail_sequence() == 0


def test_legacy_inline_payload_cannot_hide_a_missing_record(tmp_path):
    from mishe_tauftauf.records import payload, reference
    entry = FeedEntry(1, 'time', 'seed', '[task-event] ready\n{"reason":"checked"}\nChecked.\n' + reference('a'*64), tmp_path)
    with pytest.raises(ValueError, match='record|mixed'):
        payload(entry)


@pytest.mark.parametrize("damage", ["missing", "tampered", "symlink"])
def test_record_damage_fails_closed(tmp_path, damage):
    from mishe_tauftauf.records import payload
    entry = Feed(tmp_path).append_record("witness", "Checked producer evidence.", {"ok": True})
    path = next((tmp_path / "records").glob("*.json"))
    if damage == "missing":
        path.unlink()
    elif damage == "tampered":
        path.chmod(0o600)
        path.write_text('{}')
    else:
        data = path.read_bytes()
        path.unlink()
        outside = tmp_path / "outside.json"
        outside.write_bytes(data)
        path.symlink_to(outside)
    with pytest.raises(ValueError):
        payload(entry)


def test_reference_requires_home_and_canonical_path(tmp_path):
    from mishe_tauftauf.records import payload
    entry = Feed(tmp_path).append_record("witness", "Checked evidence.", {"ok": True})
    with pytest.raises(ValueError):
        payload(replace(entry, home=None))
    with pytest.raises(ValueError):
        payload(replace(entry, body=entry.body.replace("records/", "records/../")))


def test_orphan_has_no_feed_effect(tmp_path):
    from mishe_tauftauf.records import prepare
    prepare(tmp_path, {"identity": "orphan"})
    assert Feed(tmp_path).entries() == []


def test_task_event_converts_inline_payload(tmp_path):
    from mishe_tauftauf.records import payload
    data = {"reason": "Producer completed verification.", "evidence": "/tmp/check", "evidence_sha256": "a" * 64}
    entry = Feed(tmp_path).append_task_control("garden", "[task-event] checked\n" + json.dumps(data))
    assert "Producer completed verification." in entry.body
    assert '{' not in entry.body
    assert payload(entry) == data


def test_invalid_control_does_not_store_record(tmp_path):
    with pytest.raises(ValueError):
        Feed(tmp_path).append_task_control("garden", '[task-event] checked\n{}')
    assert not (tmp_path / "records").exists()


def test_records_directory_symlink_rejected(tmp_path):
    from mishe_tauftauf.records import prepare
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "records").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        prepare(tmp_path, {"ok": True})
    assert list(outside.iterdir()) == []


def test_record_size_and_existing_corruption_rejected(tmp_path):
    from mishe_tauftauf.records import prepare
    with pytest.raises(ValueError):
        prepare(tmp_path, {"large": "a" * (1024 * 1024)})
    ref = prepare(tmp_path, {"ok": True})
    assert prepare(tmp_path, {"ok": True}) == ref
    path = next((tmp_path / "records").glob("*.json"))
    path.chmod(0o600)
    path.write_text('{}')
    with pytest.raises(ValueError):
        prepare(tmp_path, {"ok": True})


def test_home_does_not_affect_entry_equality(tmp_path):
    a = FeedEntry(1, "time", "seed", "Checked.")
    assert replace(a, home=tmp_path) == a
