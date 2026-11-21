"""A non-object registry entry (``"egress": null``) must not crash the readers beside it.

signal-coverage names and judges the malformed entry (UNKNOWN); the store's readers (next expiry,
expiry sweep, subscriber admission) skip it, so the other signals still expire and wake on time. Writers
keep the malformed entry on disk so an unrelated register or expiry does not erase the evidence.
"""

import json

from mishe_tauftauf import signal_store as store
from mishe_tauftauf.live_signal import UNKNOWN

NOW = 1_800_000_000.0


def _corrupt(home, name):
    path = home / "signals" / "registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[name] = None
    path.write_text(json.dumps(data), encoding="utf-8")


def test_readers_skip_null_entry_and_expiry_still_runs(tmp_path):
    store.register(tmp_path, "coverage", max_age=3600, subscribers=["health"], now=NOW)
    store.register(tmp_path, "egress", max_age=7200, now=NOW)
    store.observe(tmp_path, "coverage", "GREEN", now=NOW, source="unit")
    _corrupt(tmp_path, "egress")

    assert "egress" not in store.registry(tmp_path)
    assert store.next_expiry(tmp_path) == NOW + 3600
    assert store.expire(tmp_path, now=NOW + 3601) == ["coverage"]
    assert store.read(tmp_path, "coverage", now=NOW + 3601).state == UNKNOWN


def test_writes_keep_the_malformed_entry_on_disk(tmp_path):
    store.register(tmp_path, "coverage", max_age=3600, subscribers=["health"], now=NOW)
    _corrupt(tmp_path, "egress")

    store.register(tmp_path, "other", max_age=60, now=NOW)
    store.expire(tmp_path, now=NOW + 1)

    on_disk = json.loads((tmp_path / "signals" / "registry.json").read_text(encoding="utf-8"))
    assert on_disk["egress"] is None
    assert "other" in on_disk and "coverage" in on_disk
