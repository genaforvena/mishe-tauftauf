import hashlib
import json

from mishe_tauftauf.feed import Feed
from mishe_tauftauf.observations import redact_display_age
from mishe_tauftauf.seed_board import open_tasks


def test_redact_display_age_keeps_meaning_and_unknown_ages():
    held = "UNKNOWN sense.space.light: Note3 light UNKNOWN: light-not-current-or-delayed last-phone=2026-10-11T00:47:31+00:00 age=19m17s; source_state=unknown"
    assert redact_display_age(held) == held.replace("age=19m17s", "age=<age>").replace("last-phone=2026-10-11T00:47:31+00:00", "last-phone=<phone>")
    # A growing number is redacted the same way whether seconds or minutes.
    assert redact_display_age(held.replace("age=19m17s", "age=5s")) == redact_display_age(held)
    # A non-numeric age is distinct and preserved.
    assert redact_display_age("UNKNOWN sense.x: age=unknown") == "UNKNOWN sense.x: age=unknown"
    # Lines without an age token pass through unchanged.
    assert redact_display_age("STATE: GREEN") == "STATE: GREEN"
    # Every other token on the line is preserved, including the class wording.
    redacted = redact_display_age(held)
    assert "light-not-current-or-delayed" in redacted
    assert "last-phone=<phone>" in redacted
    assert "last-phone=2026-10-11T00:47:31+00:00" not in redacted
    assert "19m17s" not in redacted
    # Idle seconds in HEADLINE are volatile per-tick and must be redacted.
    headline_1 = "HEADLINE: GREEN — mind ok idle 7s; watcher live"
    headline_2 = "HEADLINE: GREEN — mind ok idle 42s; watcher live"
    assert redact_display_age(headline_1) == "HEADLINE: GREEN — mind ok idle <idle>; watcher live"
    assert redact_display_age(headline_1) == redact_display_age(headline_2)
    # Idle redaction preserves the rest of the line.
    assert "watcher live" in redact_display_age(headline_1)


def test_indented_task_tags_remain_visible_and_addressed(tmp_path):
    feed = Feed(tmp_path)
    task = feed.append("senses", " [task] sample-io owner=genome source=/proc/pressure/io acceptance=checked-sense retry=source-change")
    assert [(item.identity, item.status) for item in open_tasks(feed.entries())] == [("sample-io", "open")]
    feed.append("genome", " [taking] sample-io — reviewing the reading")
    assert [(item.identity, item.status) for item in open_tasks(feed.entries())] == [("sample-io", "taking")]
    feed.append("genome", " [done] sample-io — checked source and live pane")
    assert open_tasks(feed.entries()) == []

def test_duplicate_task_announcement_does_not_reopen_terminal_task(tmp_path):
    feed = Feed(tmp_path)
    feed.append("genome", "[task] task-board-terminal-state-3296 owner=genome source=chat acceptance=terminal retry=change")
    feed.append("genome", "[done] task-board-terminal-state-3296 — checked")
    feed.append("witness", "[task] task-board-terminal-state-3296 owner=genome source=chat acceptance=terminal retry=change")
    assert open_tasks(feed.entries()) == []








def _digest(text):
    return hashlib.sha256(json.dumps({"sensor": text, "command_ok": True}, sort_keys=True).encode()).hexdigest()










