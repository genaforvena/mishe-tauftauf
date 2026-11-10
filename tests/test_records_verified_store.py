"""Verified-payload store: hits only while record identity holds; damage fails closed."""
import os
import pytest
from mishe_tauftauf import records
from mishe_tauftauf import feed as feed_module
from mishe_tauftauf.feed import Feed, parse_feed
from tests.tape_binary import pin_tape_binary

pytestmark = pytest.mark.usefixtures("pin_tape_binary")


@pytest.fixture(autouse=True)
def fresh_process_caches(monkeypatch):
    """Each Feed call behaves like a new render process: no in-process tape cache."""
    original = Feed.entries

    def entries(self, *args, **kwargs):
        feed_module._ENTRIES_CACHE.clear()
        feed_module._ROLE_ENTRIES_CACHE.clear()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Feed, "entries", entries)
    yield


def _seed(home, n=3):
    feed = Feed(home)
    for i in range(n):
        feed.append_record("witness", f"Checked record {i} is available for review.", {"i": i, "tag": "x" * 40})
    return feed


def _record_file(home, digest_source_entry):
    digest = records.REFERENCE_RE.fullmatch(
        [l for l in digest_source_entry.body.splitlines() if l.startswith("[record]")][0]).group(1)
    return home / "records" / (digest + ".json"), digest


def test_cold_and_warm_payloads_are_identical(tmp_path):
    _seed(tmp_path)
    cold = Feed(tmp_path).entries(payloads={})
    store = records.verified_store(tmp_path)
    assert store.path.exists()
    warm_sink = {}
    warm = Feed(tmp_path).entries(payloads=warm_sink)
    assert [e.body for e in cold] == [e.body for e in warm]
    expected = [records.payload(e) for e in parse_feed(Feed(tmp_path).read_bytes(), home=tmp_path)]
    assert [records.payload(e) for e in warm] == expected


def test_hit_is_served_from_store_without_envelope(tmp_path, monkeypatch):
    entries = _seed(tmp_path).entries()
    records.payload(entries[0])  # warm store through the normal path
    store_entry = entries[0]
    seen = []
    real = records.envelope
    monkeypatch.setattr(records, "envelope", lambda e, *a, **k: seen.append(1) or real(e, *a, **k))
    for e in entries:
        records.cached_payload(e, {}, records.verified_store(tmp_path))
    assert seen == []  # every identity still matches the stored verification


def test_verification_only_does_not_decode_cached_payload(tmp_path, monkeypatch):
    entries = _seed(tmp_path).entries()
    monkeypatch.setattr(records.zlib, "decompress", lambda _: pytest.fail("unneeded payload decode"))
    Feed._verify_records(entries)


def test_verification_only_still_detects_changed_record(tmp_path):
    entries = _seed(tmp_path).entries()
    path, _ = _record_file(tmp_path, entries[0])
    path.chmod(0o600)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum|record"):
        Feed._verify_records(entries)


def test_in_place_rewrite_is_detected(tmp_path):
    entries = _seed(tmp_path).entries()
    for e in entries:
        records.cached_payload(e, {}, records.verified_store(tmp_path))
    path, _ = _record_file(tmp_path, entries[1])
    original = path.read_bytes()
    path.chmod(0o600)
    path.write_bytes(original.replace(b'"i":1', b'"i":9'))  # size may match; ctime moves
    with pytest.raises(ValueError, match="checksum|record"):
        Feed(tmp_path).entries()


def test_missing_record_still_fails_closed_after_warm_store(tmp_path):
    entries = _seed(tmp_path).entries()
    Feed(tmp_path).entries(payloads={})
    path, _ = _record_file(tmp_path, entries[0])
    path.chmod(0o600)
    path.unlink()
    with pytest.raises(ValueError, match="record"):
        Feed(tmp_path).entries(payloads={})


def test_replaced_identical_record_is_reverified_not_trusted(tmp_path):
    entries = _seed(tmp_path).entries()
    Feed(tmp_path).entries(payloads={})
    path, _ = _record_file(tmp_path, entries[0])
    data = path.read_bytes()
    replacement = path.with_name(path.name + ".new")
    replacement.write_bytes(data)
    os.replace(replacement, path)  # same bytes, new inode: must be reverified
    assert Feed(tmp_path).entries(payloads={})[0].sequence == 1


def test_corrupt_store_falls_back_to_full_verification(tmp_path):
    _seed(tmp_path)
    Feed(tmp_path).entries(payloads={})
    store = records.verified_store(tmp_path)
    store._connection = None
    store._broken = False
    store.path.write_bytes(b"not a database" * 100)  # the store is not a sqlite file
    sink = {}
    got = Feed(tmp_path).entries(payloads=sink)
    assert len(got) == 3 and len(sink) == 3


@pytest.mark.parametrize("column,value", [("verified_at", "bad"), ("verified_at", None),
                                           ("payload", "bad"), ("payload", b"bad")])
def test_malformed_cache_row_reverifies_canonical_record(tmp_path, column, value):
    entries = _seed(tmp_path, 1).entries()
    store = records.verified_store(tmp_path)
    with store._connection:
        store._connection.execute(f"UPDATE verified SET {column}=?", (value,))
    sink = {}
    Feed._verify_records(entries, sink)
    assert sink[entries[0].sequence]["i"] == 0

def test_non_integer_sqlite_identity_is_reverified(tmp_path, monkeypatch):
    entries = _seed(tmp_path, 1).entries()
    store = records.verified_store(tmp_path)
    connection = store._connection
    connection.execute("ALTER TABLE verified RENAME TO verified_old")
    connection.execute(
        "CREATE TABLE verified (digest, ino, size, ctime_ns, mtime_ns, "
        "verified_at, payload)")
    connection.execute(
        "INSERT INTO verified SELECT digest, CAST(ino AS REAL), size, ctime_ns, "
        "mtime_ns, verified_at, payload FROM verified_old")
    connection.execute("DROP TABLE verified_old")

    verified = []
    original = records.envelope
    monkeypatch.setattr(records, "envelope",
                        lambda entry, *a, **k: verified.append(entry) or original(entry, *a, **k))
    Feed._verify_records(entries)

    assert verified == entries

def test_stale_entry_past_max_age_is_reverified(tmp_path, monkeypatch):
    entries = _seed(tmp_path).entries()
    Feed(tmp_path).entries(payloads={})
    monkeypatch.setattr(records, "VERIFIED_MAX_AGE", 0)
    seen = []
    real = records.envelope
    monkeypatch.setattr(records, "envelope", lambda e, *a, **k: seen.append(1) or real(e, *a, **k))
    Feed(tmp_path).entries(payloads={})
    assert len(seen) == 3


def test_legacy_inline_payload_bypasses_store(tmp_path):
    from mishe_tauftauf.feed import FeedEntry
    legacy = FeedEntry(1, "time", "seed", '[task-event] ready\n{"reason":"checked"}\nChecked.', tmp_path)
    assert records.cached_payload(legacy, {}) == {"reason": "checked"}


def test_symlinked_records_directory_fails_the_pass(tmp_path):
    entries = _seed(tmp_path, 2).entries()
    outside = tmp_path / "outside"
    outside.mkdir()
    import shutil
    shutil.move(str(tmp_path / "records"), str(outside / "records"))
    (tmp_path / "records").symlink_to(outside / "records", target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        Feed._verify_records(entries)


def test_records_swapped_for_symlink_mid_pass_is_not_green_and_not_stored(tmp_path, monkeypatch):
    entries = _seed(tmp_path, 3).entries()
    store = records.verified_store(tmp_path)
    real_store = store.store
    stored = []
    monkeypatch.setattr(store, "store", lambda rows: stored.append(rows) or real_store(rows))
    monkeypatch.setattr(feed_module, "VERIFIED_FLUSH_ROWS", 1)
    swapped = []
    real_cached = feed_module.cached_payload

    def swap_after_first(entry, pending, store=None, **kwargs):
        result = real_cached(entry, pending, store, **kwargs)
        if not swapped:
            swapped.append(True)
            live = tmp_path / "records"
            moved = tmp_path / "records-old"
            os.rename(live, moved)
            live.symlink_to(moved, target_is_directory=True)
        return result

    monkeypatch.setattr(feed_module, "cached_payload", swap_after_first)
    with pytest.raises(ValueError, match="replaced|unavailable"):
        Feed._verify_records(entries)
    assert stored == []  # nothing from the old inode is persisted


def test_pass_directory_fd_is_closed_after_success(tmp_path, monkeypatch):
    entries = _seed(tmp_path, 2).entries()
    opened = []
    real_open = records._directory
    monkeypatch.setattr(records, "_directory", lambda home: opened.append(1) or real_open(home))
    before = len(os.listdir("/proc/self/fd"))
    Feed._verify_records(entries)
    assert len(os.listdir("/proc/self/fd")) == before
    assert len(opened) <= 2


def test_every_entry_carries_the_pass_home(tmp_path):
    """The reader hands each entry the pass's site home, not a per-entry rebuild."""
    _seed(tmp_path, 2)
    entries = Feed(tmp_path).entries()
    assert [entry.home for entry in entries] == [tmp_path, tmp_path]


def test_a_record_under_another_home_is_verified_against_its_own_directory(tmp_path):
    """A pass that meets a record from another site must not read it through this site's fd.

    The shared directory fd is only valid for the site that opened it. An entry
    whose home differs must open its own, or the wrong site's record file is
    read and a digest that belongs to another site is reported as verified here.
    """
    _seed(tmp_path, 1)                       # this site has a records directory too
    other = tmp_path / "other"
    other.mkdir()
    foreign = Feed(other)
    foreign.append_record("witness", "Checked evidence from another site.", {"site": "other"})

    entry = foreign.entries()[0]
    # The entry names the other site, but the pass was opened for this site.
    assert entry.home == other
    shared_dir = records._directory(tmp_path)
    try:
        with pytest.raises((ValueError, OSError)):
            records.cached_payload(entry, {}, records.verified_store(tmp_path),
                                  directory=shared_dir)
    finally:
        os.close(shared_dir)


def test_the_same_home_is_shared_and_a_different_home_is_not(tmp_path):
    """The pass's directory fd is reused for its own home and withheld from another."""
    other = tmp_path / "other"
    other.mkdir()
    mine = _seed(tmp_path, 1).entries()
    theirs = Feed(other).append_record("witness", "Checked evidence elsewhere.", {"site": "other"})

    seen = []
    real_cached = feed_module.cached_payload

    def watch(entry, pending, store=None, **kwargs):
        seen.append((entry.home, kwargs.get("directory")))
        return real_cached(entry, pending, store, **kwargs)

    feed_module.cached_payload = watch
    try:
        Feed._verify_records(mine + [theirs])
    finally:
        feed_module.cached_payload = real_cached

    mine_dir, theirs_dir = seen[0][1], seen[1][1]
    assert mine_dir is not None      # this site's record reused the pass's fd
    assert theirs_dir is None        # the other site's record opened its own


def test_the_role_read_verifies_the_whole_tape_in_one_pass(tmp_path):
    """The role read must not restart a verification pass for every entry.

    A pass per entry meant a records-directory descriptor, an lstat of the
    live records path and a store commit for each of the tape's entries: on
    the live tape, 299,304 passes and 196,303 commits for one cold read. The
    role read now runs one pass, so the descriptor is opened once.
    """
    feed = Feed(tmp_path)
    for i in range(6):
        feed.append_record("witness", f"Checked record {i} is available.", {"i": i})

    opened = []
    real_open = records._directory
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(records, "_directory", lambda home: opened.append(1) or real_open(home))
        view = feed.role_entries("genome")
    finally:
        monkeypatch.undo()
    assert view
    assert len(opened) <= 2, "the role read must open the records directory once, not once per entry"


def test_a_batch_verifies_a_repeated_record_against_its_current_identity(tmp_path):
    """A digest already verified in a batch is verified against the file as it is now.

    Batching shares the pending rows across the entries of a batch, so a
    second reference to the same digest can be served from the batch. The
    file's identity must still be checked before its payload is served: a
    record rewritten after the first reference is verified again, not served
    from the batch.
    """
    feed = Feed(tmp_path)
    entry = feed.append_record("witness", "Checked evidence is available.", {"i": 0})
    path, _ = _record_file(tmp_path, entry)

    pending = {}
    store = records.verified_store(tmp_path)
    directory = records._directory(tmp_path)
    try:
        records.cached_payload(entry, pending, store, verify_only=True, directory=directory)
        assert entry.sequence  # the digest is now pending in this batch
        original = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(original.replace(b'"i":0', b'"i":9'))  # same size, new ctime
        with pytest.raises(ValueError, match="checksum|record"):
            records.cached_payload(entry, pending, store, verify_only=False, directory=directory)
    finally:
        os.close(directory)


def test_a_replaced_records_directory_is_noticed_within_the_batch_bound(tmp_path, monkeypatch):
    """The swap check fires at the batch bound, and the pass stores nothing from the old inode."""
    feed = Feed(tmp_path)
    for i in range(4):
        feed.append_record("witness", f"Checked record {i} is available.", {"i": i})
    entries = feed.entries()

    store = records.verified_store(tmp_path)
    stored = []
    real_store = store.store
    monkeypatch.setattr(store, "store", lambda rows: stored.append(rows) or real_store(rows))
    # One flush for the whole batch: the swap happens after the first entry.
    monkeypatch.setattr(feed_module, "VERIFIED_FLUSH_ROWS", 1000)
    monkeypatch.setattr(feed_module, "VERIFIED_BATCH_ENTRIES", 2)

    swapped = []
    real_cached = feed_module.cached_payload

    def swap_after_first(entry, pending, store=None, **kwargs):
        result = real_cached(entry, pending, store, **kwargs)
        if not swapped:
            swapped.append(True)
            live = tmp_path / "records"
            moved = tmp_path / "records-old"
            os.rename(live, moved)
            live.symlink_to(moved, target_is_directory=True)
        return result

    monkeypatch.setattr(feed_module, "cached_payload", swap_after_first)
    with pytest.raises(ValueError, match="replaced|unavailable"):
        Feed._verify_records(entries)
    assert stored == []  # nothing from the old inode is persisted
