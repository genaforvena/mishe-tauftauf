"""wall.settle primes the role view before it takes the plant-wide seed lock.

Health measured a cold role-view read of the 137 MB chat.log at about 14 s;
inside seed._lock that starves every other seed holder. The priming read must
happen before the lock is taken, and settlement must still write its receipt.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from mishe_tauftauf import seed
from mishe_tauftauf.chat_protocol import decode_lifecycle
from mishe_tauftauf.feed import Feed


def _setup(home):
    (home / "coordination-mode.json").write_text(json.dumps({
        "mode": "wall",
        "until": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
    }))


def test_role_view_is_read_before_seed_lock_is_taken(tmp_path, monkeypatch):
    _setup(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    note = tmp_path / "plan.md"
    note.write_text("plan\n")

    events = []
    real_lock = seed._lock
    real_role_entries = Feed.role_entries

    @contextmanager
    def recording_lock(home):
        events.append("lock")
        with real_lock(home):
            yield

    def recording_role_entries(self, role, *args, **kwargs):
        events.append("read")
        return real_role_entries(self, role, *args, **kwargs)

    monkeypatch.setattr(seed, "_lock", recording_lock)
    monkeypatch.setattr(Feed, "role_entries", recording_role_entries)

    seed.yield_wake(tmp_path, "genome", wake.sequence, note, continue_task=False, result="verified")

    assert events.index("read") < events.index("lock"), events
    receipts = [e for e in Feed(tmp_path).entries()
                if (c := decode_lifecycle(e)) and c.kind == "yield" and c.role == "genome"]
    assert len(receipts) == 1
