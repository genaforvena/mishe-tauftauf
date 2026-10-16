"""The session-watch routing checker reads durable detector artifacts, not the journal.

`session_watch.route()` writes `health/session-watch-alert.md` and then appends a
DM through the guarded CLI. Once the wall-role guard landed, the append fails
with exit 2 while the alert file keeps being refreshed every 120s, so the only
trace was the service journal and a resolver that never learned of the
condition. These tests pin the checker's contract on fixtures: a fresh alert
newer than the newest routed DM is RED, a routed alert is GREEN, and a missing,
stale, malformed or GREEN detector reads UNKNOWN instead of success — a detector
with nothing to route cannot show whether the channel works.
"""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

_CHECKOUT_ROOT = Path(__file__).resolve().parent.parent
_CHECKER = _CHECKOUT_ROOT / "tools" / "check_session_watch_routing.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("_session_watch_routing_checker", _CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_checker()
_FEED = _MODULE.feed

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()
_ALERT_BODY = "Session watcher RED: session identity changed within the last hour\n"
_DM_BODY = "[dm] to=health\n" + _ALERT_BODY


def _stamp(offset: float) -> str:
    moment = datetime.fromtimestamp(NOW + offset, timezone.utc)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _home(tmp_path, rows=(), state=None, alert_offset=None, tape=True):
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    if tape:
        data = b"".join(
            _FEED._encode_entry(index, stamp, source, body)
            for index, (stamp, source, body) in enumerate(rows, start=1))
        (home / "chat.log").write_bytes(data)
    if state is not None:
        (home / "health" / "session-watch.json").write_text(json.dumps(state))
    if alert_offset is not None:
        path = home / "health" / "session-watch-alert.md"
        path.write_text(_ALERT_BODY)
        stamp = NOW + alert_offset
        os.utime(path, (stamp, stamp))
    return home


def _red_state(observed_offset=-20.0, onset_offset=-600.0):
    return {"identity": "1:$0:1", "available": True, "panes_live": True,
            "dashboard_live": True, "observed_at": NOW + observed_offset,
            "state": "RED", "reason": "session identity changed within the last hour",
            "changes": [NOW + onset_offset]}


def test_alert_newer_than_the_newest_routed_dm_is_red(tmp_path):
    home = _home(tmp_path, rows=[(_stamp(-3600), "session-watch", _DM_BODY)],
                 state=_red_state(), alert_offset=-30)
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "RED"
    assert report["exit"] == 2
    assert any("not routed" in text for text in report["failures"]), report["failures"]
    assert report["newest_routed"]["timestamp"] == _stamp(-3600).replace("Z", "+00:00")


def test_routed_alert_is_green(tmp_path):
    home = _home(tmp_path, rows=[(_stamp(-25), "session-watch", _DM_BODY)],
                 state={"observed_at": NOW - 20, "state": "GREEN", "changes": []},
                 alert_offset=-30)
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "GREEN"
    assert report["exit"] == 0
    assert report["failures"] == []


def test_green_detector_is_unknown_not_success(tmp_path):
    home = _home(tmp_path, rows=[(_stamp(-3600), "session-watch", _DM_BODY)],
                 state={"observed_at": NOW - 20, "state": "GREEN", "changes": []})
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "UNKNOWN"
    assert report["exit"] == 3
    assert any("nothing to route" in text for text in report["limits"]), report["limits"]


def test_stale_state_and_alert_are_unknown(tmp_path):
    home = _home(tmp_path, rows=[(_stamp(-7200), "session-watch", _DM_BODY)],
                 state=_red_state(observed_offset=-3600.0, onset_offset=-7200.0),
                 alert_offset=-3600)
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "UNKNOWN"
    assert report["exit"] == 3
    assert sum("stale" in text for text in report["limits"]) == 2, report["limits"]


def test_red_state_without_any_routed_dm_is_red(tmp_path):
    home = _home(tmp_path, rows=[(_stamp(-60), "witness", "[dm] to=health\nunrelated\n")],
                 state=_red_state())
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "RED"
    assert report["exit"] == 2
    assert any("state RED since" in text for text in report["failures"]), report["failures"]


def test_non_dm_entry_does_not_count_as_routed(tmp_path):
    home = _home(tmp_path, rows=[(_stamp(-3600), "session-watch", _ALERT_BODY)],
                 state=_red_state(), alert_offset=-30)
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "RED"
    assert report["routed_dm_count"] == 0
    assert any("exists anywhere on the tape" in text for text in report["failures"])


def test_missing_tape_is_unknown(tmp_path):
    home = _home(tmp_path, state=_red_state(), alert_offset=-30, tape=False)
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "UNKNOWN"
    assert report["exit"] == 3
    assert any("tape missing" in text for text in report["limits"]), report["limits"]


def test_malformed_tape_is_unknown(tmp_path):
    home = _home(tmp_path, state=_red_state(), alert_offset=-30)
    (home / "chat.log").write_bytes(b"v1 not-a-header\n")
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "UNKNOWN"
    assert report["exit"] == 3
    assert any("undecodable" in text for text in report["limits"]), report["limits"]


def test_missing_state_and_alert_is_unknown(tmp_path):
    home = _home(tmp_path, rows=[(_stamp(-3600), "session-watch", _DM_BODY)])
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "UNKNOWN"
    assert report["exit"] == 3
    assert any("detector state missing" in text for text in report["limits"])
    assert any("no alert file" in text for text in report["limits"])


def test_check_writes_nothing(tmp_path):
    home = _home(tmp_path, rows=[(_stamp(-3600), "session-watch", _DM_BODY)],
                 state=_red_state(), alert_offset=-30)
    before = {path: (path.stat().st_size, path.stat().st_mtime_ns)
              for path in sorted(home.rglob("*")) if path.is_file()}
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    after = {path: (path.stat().st_size, path.stat().st_mtime_ns)
             for path in sorted(home.rglob("*")) if path.is_file()}
    assert report["exit"] == 2
    assert before == after


def test_unrouted_alert_is_red_even_when_the_detector_reads_green(tmp_path):
    """The channel signal is state-independent: an unrouted alert stays a failure
    after the RED episode ages out, for as long as the attempt is fresh."""
    home = _home(tmp_path, rows=[(_stamp(-3600), "session-watch", _DM_BODY)],
                 state={"observed_at": NOW - 20, "state": "GREEN", "changes": []},
                 alert_offset=-30)
    report = _MODULE.check(home, NOW, 60.0, 600.0)
    assert report["verdict"] == "RED"
    assert report["exit"] == 2
    assert any("not routed" in text for text in report["failures"]), report["failures"]
