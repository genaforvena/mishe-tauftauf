from mishe_tauftauf.feed import Feed
from mishe_tauftauf.seed import _observation_text, _state
from mishe_tauftauf.seed_board import open_tasks


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
