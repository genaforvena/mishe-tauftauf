import hashlib
import json

from mishe_tauftauf.feed import Feed
from mishe_tauftauf.observations import redact_display_age
from mishe_tauftauf.seed import _observation_text, _state
from mishe_tauftauf.seed_board import open_tasks


def test_redact_display_age_keeps_meaning_and_unknown_ages():
    held = "UNKNOWN sense.space.light: Note3 light UNKNOWN: light-not-current-or-delayed last-phone=2026-10-11T00:47:31+00:00 age=19m17s; source_state=unknown"
    assert redact_display_age(held) == held.replace("age=19m17s", "age=<age>")
    # A growing number is redacted the same way whether seconds or minutes.
    assert redact_display_age(held.replace("age=19m17s", "age=5s")) == redact_display_age(held)
    # A non-numeric age is distinct and preserved.
    assert redact_display_age("UNKNOWN sense.x: age=unknown") == "UNKNOWN sense.x: age=unknown"
    # Lines without an age token pass through unchanged.
    assert redact_display_age("STATE: GREEN") == "STATE: GREEN"
    # Every other token on the line is preserved, including the class wording.
    redacted = redact_display_age(held)
    assert "light-not-current-or-delayed" in redacted
    assert "last-phone=2026-10-11T00:47:31+00:00" in redacted
    assert "19m17s" not in redacted


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


def test_explained_supervisor_receipts_still_replay(tmp_path):
    feed = Feed(tmp_path)
    digest = "a" * 64
    observation = feed.append("seed", f"seed observation senses sha256={digest}\n"
                              "The senses pane changed to UNKNOWN and needs a checked read.")
    wake = feed.append("seed", f"seed wake senses observation={observation.sequence}\n"
                       "The supervisor asked for one bounded step.")
    state = _state(tmp_path, "senses")
    assert state[0] == digest
    assert state[1] == wake.sequence
    feed.append("seed", f"seed yield senses wake={wake.sequence}\nThe mind left its handoff.")
    assert _state(tmp_path, "senses")[1] is None


def test_scan_values_do_not_create_new_exploration_obligation():
    first = "GOAL: look\nVERIFIED sense.load: 1\nSCAN: first\nUNKNOWN sense.keyboard: unavailable\nSTATE: UNKNOWN\n"
    second = "GOAL: look\nVERIFIED sense.load: 2\nSCAN: second\nUNKNOWN sense.keyboard: unavailable\nSTATE: UNKNOWN\n"
    assert _observation_text("senses", first) == _observation_text("senses", second)
    assert _observation_text("genome", first) != _observation_text("genome", second)


def test_a_drift_sense_line_reaches_the_senses_observation_text():
    frame = ("GOAL: look\n"
             "DRIFT sense.runtime.renderer-coverage: renderers=9 "
             "script_unknown=self-development source_state=drift\n"
             "VERIFIED sense.proc.loadavg: 9.76 12.71 20.45 source_state=verified\n"
             "STATE: UNKNOWN — 5 senses need a checked read or honest unavailable claim\n")
    reduced = _observation_text("senses", frame)
    assert "DRIFT sense.runtime.renderer-coverage" in reduced
    assert "VERIFIED sense.proc.loadavg" not in reduced
    assert "STATE: UNKNOWN" in reduced


def _digest(text):
    return hashlib.sha256(json.dumps({"sensor": text, "command_ok": True}, sort_keys=True).encode()).hexdigest()


def test_a_growing_display_age_does_not_refire_the_observation_digest():
    # The 2026-10-11 CHAT RATE RED flood: while the Note3 light stream held
    # frozen (cursor constant, frame writer live), every refresh grew only the
    # `age=` display value, so the discover/senses digest changed each tick and
    # each tick appended a `seed observation` chat event.
    frame = ("GOAL: discover\n"
             "UNKNOWN sense.space.light: Note3 light UNKNOWN: light-not-current-or-delayed "
             "last-phone=2026-10-11T00:47:31+00:00 age=AGEVAL; "
             "provenance=http://127.0.0.1:8765/node/note3 source_state=unknown\n"
             "STATE: UNKNOWN — 5 source reads unverified\n")
    first = _observation_text("discover", frame.replace("AGEVAL", "5s"))
    later = _observation_text("discover", frame.replace("AGEVAL", "19m17s"))
    senses_first = _observation_text("senses", frame.replace("AGEVAL", "5s"))
    senses_later = _observation_text("senses", frame.replace("AGEVAL", "19m17s"))
    assert first == later
    assert senses_first == senses_later
    assert _digest(first) == _digest(later)
    assert _digest(senses_first) == _digest(senses_later)


def test_a_real_meaning_change_still_refires_the_observation_digest():
    held = ("STATE: UNKNOWN — 5 source reads unverified\n"
            "UNKNOWN sense.space.light: Note3 light UNKNOWN: light-not-current-or-delayed "
            "last-phone=2026-10-11T00:47:31+00:00 age=19m17s; source_state=unknown\n")
    # Class change on the same frozen cursor: the seconds-gap wording is a
    # different state and must reach the digest.
    null_gap = held.replace("light-not-current-or-delayed", "light-null-gap")
    assert _observation_text("senses", held) != _observation_text("senses", null_gap)
    # Recovery: a fresh last-phone stamp is meaning too.
    resumed = held.replace("2026-10-11T00:47:31+00:00", "2026-10-11T01:20:05+00:00")
    assert _observation_text("senses", held) != _observation_text("senses", resumed)
    assert _observation_text("senses", held) != _observation_text(
        "senses", held.replace("STATE: UNKNOWN", "STATE: GREEN"))
