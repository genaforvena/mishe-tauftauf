"""Record-kind index: a cached kind is served only while the record's identity holds."""
import json
import os
from pathlib import Path

import pytest

from mishe_tauftauf import records


@pytest.fixture(autouse=True)
def private_cache(tmp_path, monkeypatch):
    """Each test gets its own user cache, so the index never touches a real one."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


def _write_record(home: Path, kind: str, body: str) -> str:
    """Write a content-addressed record the way the store names them; return its digest."""
    import hashlib
    raw = json.dumps({"kind": kind, "payload": {"text": body}, "version": 1},
                     sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    (home / "records").mkdir(exist_ok=True)
    (home / "records" / (digest + ".json")).write_bytes(raw)
    return digest


def _direct(home: Path, digest: str) -> str | None:
    """The uncached answer: the head read straight from the record file."""
    try:
        with open(home / "records" / (digest + ".json"), "rb") as handle:
            return records.record_kind(handle.read(records.HEAD_BYTES))
    except OSError:
        return None


def _kind_via_index(home: Path, index: records.KindIndex, digest: str) -> str | None:
    fd = records._directory(home)
    try:
        return index.kind(fd, digest)
    finally:
        os.close(fd)


def test_cold_then_warm_matches_direct_read(tmp_path):
    digest = _write_record(tmp_path, "wall-outcome", "done")
    first = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, first, digest) == "wall-outcome"
    first.close()

    second = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, second, digest) == _direct(tmp_path, digest) == "wall-outcome"
    second.close()


def test_rewritten_record_misses_and_reads_the_new_head(tmp_path):
    digest = _write_record(tmp_path, "wall-outcome", "done")
    index = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, index, digest) == "wall-outcome"
    index.close()

    # Same name, new bytes: ctime moves, so the stored identity no longer matches.
    path = tmp_path / "records" / (digest + ".json")
    path.write_bytes(b'{"kind":"event","payload":{},"version":1}')
    later = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, later, digest) == "event"
    later.close()


def test_missing_record_is_none_and_not_cached(tmp_path):
    (tmp_path / "records").mkdir()
    digest = "0" * 64
    index = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, index, digest) is None
    index.close()
    assert _cached(tmp_path, digest) is None


def test_head_without_kind_field_is_cached_as_none(tmp_path):
    (tmp_path / "records").mkdir()
    digest = "1" * 64
    (tmp_path / "records" / (digest + ".json")).write_bytes(b'{"payload":{},"version":1}')
    index = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, index, digest) is None
    index.close()
    again = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, again, digest) is None
    again.close()


def test_symlinked_record_is_never_followed(tmp_path):
    (tmp_path / "records").mkdir()
    target = _write_record(tmp_path, "wall-outcome", "elsewhere")
    digest = "2" * 64
    os.symlink(tmp_path / "records" / (target + ".json"), tmp_path / "records" / (digest + ".json"))
    index = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, index, digest) is None
    index.close()


def test_damaged_index_falls_back_to_direct_read(tmp_path):
    digest = _write_record(tmp_path, "wall-outcome", "done")
    index = records.KindIndex(tmp_path)
    index.path.parent.mkdir(parents=True, exist_ok=True)
    index.path.write_bytes(b"not a database" * 100)
    assert _kind_via_index(tmp_path, index, digest) == "wall-outcome"
    index.close()


def _cached(home: Path, digest: str):
    import sqlite3
    path = records.kind_index_path(home)
    if not path.exists():
        return None
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT kind FROM kinds WHERE digest=?", (digest,)).fetchone()
    finally:
        connection.close()
    return row


def test_unusable_cache_root_falls_back_to_direct_read(tmp_path, monkeypatch):
    digest = _write_record(tmp_path, "wall-outcome", "done")
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))
    index = records.KindIndex(tmp_path)
    assert _kind_via_index(tmp_path, index, digest) == "wall-outcome"
    index.close()
