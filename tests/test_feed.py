from __future__ import annotations

import multiprocessing
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from mishe_tauftauf import feed as feed_module
from mishe_tauftauf.feed import Feed, FeedError, parse_feed


def append_worker(home: str, number: int) -> None:
    Feed(home).append(f"worker/{number}", f"line {number}\nsecond\n" if number % 2 else f"line {number}\nsecond")


def once_worker(home: str) -> None:
    Feed(home).append_runtime_once("mishe-tauftauf", "wake requested top-pain sensor for entry 1")


class FeedTests(unittest.TestCase):
    def test_generic_append_rejects_reserved_task_control_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("genome", "ordinary task update")
            before = feed.read_bytes()
            for body in ("[task-state] repair — source mismatch", " [task-event] ready",
                         "[task-claim] repair owner=health previous=genome",
                         "[task-close] repair\n{}", "[task-reopen] repair\n{}"):
                with self.assertRaisesRegex(FeedError, "task.*CLI|task.*control"):
                    feed.append("genome", body)
                self.assertEqual(feed.read_bytes(), before)

    def test_unchanged_full_read_reuses_the_parsed_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("genome", "first cached entry\n")
            parses = []
            original = feed_module.parse_feed

            def counting(*args, **kwargs):
                parses.append(1)
                return original(*args, **kwargs)

            with patch.object(feed_module, "parse_feed", counting):
                first = feed.entries()
                second = Feed(directory).entries()
                self.assertEqual([e.sequence for e in first], [e.sequence for e in second])
                self.assertEqual(len(parses), 1, "an unchanged feed must not be reparsed")
            feed.append("genome", "second entry invalidates the cached revision\n")
            with patch.object(feed_module, "parse_feed", counting):
                third = feed.entries()
                self.assertEqual(len(parses), 2, "a new revision must be reparsed")
            self.assertEqual([e.sequence for e in third], [1, 2])

    def test_multiline_final_newline_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            bodies = ["one", "two\n", "header-looking\n00000000000000000001 x y ::\n    .-\n"]
            for body in bodies:
                feed.append("human", body)
            self.assertEqual([entry.body for entry in feed.entries()], bodies)
            self.assertEqual([entry.sequence for entry in feed.entries()], [1, 2, 3])

    def test_concurrent_append_is_contiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            workers = [multiprocessing.Process(target=append_worker, args=(directory, number)) for number in range(16)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
                self.assertEqual(worker.exitcode, 0)
            entries = Feed(directory).entries()
            self.assertEqual(len(entries), 16)
            self.assertEqual([entry.sequence for entry in entries], list(range(1, 17)))
            self.assertEqual({entry.source for entry in entries}, {f"worker/{n}" for n in range(16)})

    def test_corruption_refuses_append(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feed"
            path.write_text("broken\n", encoding="utf-8")
            with self.assertRaises(FeedError):
                Feed(directory).append("human", "new")
            self.assertEqual(path.read_text(encoding="utf-8"), "broken\n")

    def test_reserved_sources_rejected_only_for_generic_append(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for source in ("mishe-tauftauf", "prediction/x", "observation/x"):
                with self.assertRaises(FeedError):
                    feed.append(source, "text")
            feed.append_runtime("observation/x", "text")
            self.assertEqual(feed.entries()[0].source, "observation/x")

    def test_torn_tail_fails_closed_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("human", "intact")
            with feed.path.open("ab") as handle:
                handle.write(b"v1 00000000000000000002 2026-09-23T00:00:00Z human ::\n    | torn")
            before = feed.read_bytes()
            with self.assertRaises(FeedError):
                feed.append("human", "later")
            self.assertEqual(feed.read_bytes(), before)

    def test_restart_stale_index_and_bounded_seek(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(350):
                feed.append("human", f"event {number}")
            self.assertEqual([e.sequence for e in Feed(directory).entries(start=300, limit=3)], [300, 301, 302])
            index = Path(directory) / "feed.index.json"
            old = index.read_bytes()
            feed.append("human", "new tail")
            index.write_bytes(old)
            self.assertEqual(Feed(directory).append("human", "after stale index").sequence, 352)
            self.assertEqual([e.sequence for e in Feed(directory).entries(start=350)], [350, 351, 352])

    def test_corrupt_index_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("human", "intact")
            index = Path(directory) / "feed.index.json"
            index.write_text('{"version":99}', encoding="utf-8")
            with self.assertRaises(FeedError):
                feed.append("human", "later")
            self.assertEqual(len(parse_feed(feed.read_bytes())), 1)

    def test_append_parses_only_tail_with_current_index(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(400):
                feed.append("human", f"{number} " + "x" * 1000)
            parsed_sizes = []
            real_parse = parse_feed

            def measured(data, **kwargs):
                parsed_sizes.append(len(data))
                return real_parse(data, **kwargs)

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=measured):
                feed.append("human", "last")
            self.assertTrue(parsed_sizes)
            self.assertLess(max(parsed_sizes), 2000)

    def test_versioned_frames_and_legacy_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            first = feed.append("human", "first")
            self.assertTrue(feed.read_bytes().startswith(b"v1 "))
            old = feed.read_bytes().replace(b"v1 ", b"", 1)
            feed.path.write_bytes(old)
            self.assertEqual(feed.append("human", "second").sequence, 2)
            self.assertEqual([e.body for e in feed.entries()], ["first", "second"])

    def test_crash_after_feed_fsync_before_index_replays_once(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("human", "first")
            with patch.object(feed, "_save_index", side_effect=OSError("injected index write failure")):
                with self.assertRaises(OSError):
                    feed.append("human", "durable second")
            restarted = Feed(directory)
            self.assertEqual(restarted.append("human", "third").sequence, 3)
            self.assertEqual([e.body for e in restarted.entries()], ["first", "durable second", "third"])

    def test_tail_sequence_rebuilds_stale_checkpoint_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            self.assertEqual(feed.tail_sequence(), 0)
            feed.append("human", "first")
            stale = feed.index_path.read_bytes()
            feed.append("human", "second")
            feed.index_path.write_bytes(stale)
            self.assertEqual(Feed(directory).tail_sequence(), 2)
            with feed.path.open("ab") as handle:
                handle.write(b"v1 00000000000000000003 2026-09-23T00:00:00Z human ::\n")
            with self.assertRaises(FeedError):
                feed.tail_sequence()

    def test_once_replay_uses_bounded_identity_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(400):
                feed.append("human", f"event {number} " + "x" * 1000)
            first = feed.append_runtime_once("mishe-tauftauf", "wake requested top-pain sensor for entry 1")
            parsed_sizes = []
            real_parse = parse_feed

            def measured(data, **kwargs):
                parsed_sizes.append(len(data))
                return real_parse(data, **kwargs)

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=measured):
                again = Feed(directory).append_runtime_once("mishe-tauftauf", first.body)
            self.assertEqual(again.sequence, first.sequence)
            self.assertTrue(parsed_sizes)
            self.assertLess(max(parsed_sizes), 150_000)

    def test_identity_index_rebuilds_after_crash_and_fails_closed_if_corrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            first = feed.append_runtime_once("mishe-tauftauf", "wake requested top-pain sensor for entry 1")
            identity = Path(directory) / "feed.identities.sqlite3"
            self.assertTrue(identity.exists())
            identity.unlink()
            self.assertEqual(Feed(directory).append_runtime_once("mishe-tauftauf", first.body).sequence, first.sequence)
            identity.write_bytes(b"not sqlite")
            with self.assertRaises(FeedError):
                feed.append_runtime_once("mishe-tauftauf", first.body)
            self.assertEqual(len(parse_feed(feed.read_bytes())), 1)

    def test_concurrent_once_identity_has_one_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            workers = [multiprocessing.Process(target=once_worker, args=(directory,)) for _ in range(8)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(Feed(directory).tail_sequence(), 1)

    def test_stale_identity_index_rebuilds_from_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            request = "wake requested top-pain sensor for entry 1"
            feed.append_runtime_once("mishe-tauftauf", request)
            old_index = Path(directory) / "old.sqlite3"
            shutil.copyfile(feed.identity_path, old_index)
            feed.append("human", "later")
            shutil.copyfile(old_index, feed.identity_path)
            self.assertEqual(Feed(directory).append_runtime_once("mishe-tauftauf", request).sequence, 1)
            self.assertEqual(feed.tail_sequence(), 2)

    def test_receipt_lookup_does_not_scan_feed_history(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append_runtime("mishe-tauftauf", "wake requested top-pain sensor for entry 1")
            for number in range(400):
                feed.append("human", f"event {number} " + "x" * 1000)
            parsed_sizes = []
            real_parse = parse_feed

            def measured(data, **kwargs):
                parsed_sizes.append(len(data))
                return real_parse(data, **kwargs)

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=measured):
                feed.record_dispatch_receipt("sensor", 1, "delivered", request_id="attempt", generation=1)
            self.assertTrue(parsed_sizes)
            self.assertLess(max(parsed_sizes), 150_000)


if __name__ == "__main__":
    unittest.main()
