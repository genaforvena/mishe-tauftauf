from __future__ import annotations

import fcntl
import json
import multiprocessing
import shutil
import tempfile
import threading
import time
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

    def test_full_parse_avoids_whole_tape_unicode_and_line_copies(self):
        # One astral character widens a whole decoded tape to four bytes per
        # character. Repeated cold readers used >600 MiB for a 72 MiB tape.
        # Charge only parser allocations: the caller already owns its bytes.
        import tracemalloc
        frames = [feed_module._encode_entry(n, "2026-10-23T18:00:00Z", "seed",
                                           "x" * 16384 + (" 🐾" if n == 256 else ""))
                  for n in range(1, 257)]
        data = b"".join(frames)
        del frames
        tracemalloc.start()
        try:
            entries = parse_feed(data)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(len(entries), 256)
        self.assertTrue(entries[-1].body.endswith(" 🐾"))
        self.assertLess(peak, len(data) * 3,
                        "cold parsing must avoid whole decoded/line copies")

    def test_chunked_line_reader_scans_long_lines_once(self):
        # Count the search span rather than using a machine-dependent timeout.
        # Rescanning each unfinished prefix is quadratic for valid long lines.
        class Meter:
            scanned = 0

            def search(self, text, start=0):
                self.scanned += len(text) - start
                return original.search(text, start)

            def finditer(self, text):
                self.scanned += len(text)
                return original.finditer(text)

        original = feed_module._LINE_BREAK
        meter = Meter()
        data = feed_module._encode_entry(1, "2026-10-23T18:00:00Z", "seed", "x" * (2 * 1024**2))
        with patch.object(feed_module, "_LINE_BREAK", meter):
            entries = parse_feed(data)
        self.assertEqual(entries[0].body, "x" * (2 * 1024**2))
        self.assertLess(meter.scanned, len(data) * 3,
                        "unfinished lines must not be rescanned on every chunk")

    def test_invalid_utf8_reports_absolute_tape_offset(self):
        data = feed_module._encode_entry(1, "2026-10-23T18:00:00Z", "seed", "x" * 70000)
        data += b"\xff"
        with self.assertRaises(FeedError) as error:
            parse_feed(data)
        self.assertIn("position " + str(len(data) - 1), str(error.exception))

    def test_role_view_preserves_state_messages_recent_chat_and_global_activity(self):
        from mishe_tauftauf import seed, wall, activity
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bodies = [("seed", "seed observation health sha256=" + "a" * 64),
                      ("health", "An older real mind action."),
                      ("seed", "seed wake health observation=1"),
                      ("operator", "[dm] to=health\nReconcile the pending wake.")]
            bodies += [("sensor/noise", "unrelated " + "x" * 4096) for _ in range(80)]
            bodies += [("seed", "seed observation docs sha256=" + "b" * 64),
                       ("seed", "seed yield docs wake=1"),
                       ("seed", "unknown seed evidence retained")]
            home.joinpath("chat.log").write_bytes(b"".join(
                feed_module._encode_entry(n, "2026-10-23T18:00:00Z", source, body)
                for n, (source, body) in enumerate(bodies, 1)))
            full = Feed(home).entries()
            selected = Feed(home).role_entries("health")
            self.assertEqual(seed._state(home, "health", entries=full), seed._state(home, "health", entries=selected))
            self.assertEqual([e for e in full if wall.addressed(e, "health")][-12:], [e for e in selected if wall.addressed(e, "health")][-12:])
            self.assertEqual([e for e in full if e.source != "seed"][-12:], [e for e in selected if e.source != "seed"][-12:])
            now = datetime(2026, 10, 23, 18, 10, tzinfo=timezone.utc)
            self.assertEqual(activity.observe(home, entries=full, now=now), activity.observe(home, entries=selected, now=now))
            self.assertLess(len(selected), len(full) // 2)
            self.assertNotIn(str(Feed(home).path), feed_module._ENTRIES_CACHE)

    def test_role_cache_extends_from_canonical_tail_not_last_selected_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("seed", "seed observation health sha256=" + "a" * 64)
            for n in range(59):
                feed.append("sensor/noise", "irrelevant " + str(n))
            feed.append("seed", "seed observation docs sha256=" + "b" * 64)
            first = feed.role_entries("health")
            self.assertEqual(first[-1].sequence, 60)
            feed.append("operator", "[dm] to=health\nNew evidence")
            parsed = []
            original = feed_module._iter_parsed_feed
            def measured(data, **kwargs):
                parsed.append(len(data))
                return original(data, **kwargs)
            with patch.object(feed_module, "_iter_parsed_feed", measured):
                second = feed.role_entries("health")
            self.assertEqual(second[-1].sequence, 62)
            self.assertEqual(first[0], second[0])
            self.assertLess(max(parsed), 1000)
            self.assertNotIn(str(feed.path), feed_module._ENTRIES_CACHE)

    def test_role_view_verifies_discarded_records_and_refuses_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            entry = feed.append_record("sensor/unrelated", "Unrelated checked snapshot", {"value": "old"})
            record = next((Path(directory) / "records").glob("*.json"))
            record.unlink()
            with self.assertRaises(ValueError):
                feed.role_entries("health")

    def test_role_view_rejects_a_bad_appended_unrelated_record(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("seed", "seed observation health sha256=" + "a" * 64)
            feed.role_entries("health")
            feed.append_record("sensor/unrelated", "New unrelated evidence", {"ok": True})
            next((Path(directory) / "records").glob("*.json")).unlink()
            with self.assertRaises(ValueError):
                feed.role_entries("health")

    def test_role_view_rewrite_and_corrupt_index_cannot_reuse_old_state(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("operator", "[dm] to=health\nOld message")
            feed.role_entries("health")
            feed.path.write_bytes(feed_module._encode_entry(1, "2026-10-23T18:00:00Z", "operator", "[dm] to=health\nNew message"))
            self.assertEqual(feed.role_entries("health")[0].body, "[dm] to=health\nNew message")
            feed.index_path.write_text('{"version":99}')
            with self.assertRaises(FeedError):
                feed.role_entries("health")

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

    def test_full_read_extends_the_cached_revision_with_appended_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(300):
                feed.append("human", f"event {number} " + "x" * 200)
            cached = feed.entries()
            parsed_sizes = []
            real_parse = parse_feed

            def measured(data, **kwargs):
                parsed_sizes.append(len(data))
                return real_parse(data, **kwargs)

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=measured):
                feed.append("human", "appended after the cached read")
                extended = Feed(directory).entries()
            canonical = parse_feed(feed.read_bytes())
            self.assertEqual([e.sequence for e in extended], list(range(1, 302)))
            self.assertEqual([e.body for e in extended], [e.body for e in canonical])
            self.assertEqual([e.body for e in extended[:300]], [e.body for e in cached])
            self.assertLess(max(parsed_sizes), 4000,
                            "an append must extend the cached revision, not replay the tape")

    def test_grown_tape_without_matching_checkpoint_is_reparsed(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(200):
                feed.append("human", f"old {number} " + "x" * 100)
            feed.entries()
            # An out-of-band rewrite that keeps the length but changes the history,
            # then grows the tape. The checkpoint no longer describes the file, so
            # the cached revision must not be spliced onto the new suffix.
            original = feed.read_bytes()
            rewritten = original.replace(b"| old ", b"| new ")
            self.assertIn(b"new 0", rewritten)
            self.assertEqual(len(rewritten), len(original))
            feed.path.write_bytes(rewritten + feed_module._encode_entry(
                201, "2026-10-05T00:00:00.000000Z", "human", "appended\n"))
            entries = Feed(directory).entries()
            self.assertEqual([e.sequence for e in entries], list(range(1, 202)))
            self.assertEqual(entries[0].body, "new 0 " + "x" * 100)
            self.assertEqual(entries[-1].body, "appended\n")

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

    def test_empty_identity_index_rebuilds_instead_of_breaking_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            first = feed.append_runtime_once("mishe-tauftauf", "wake requested top-pain sensor for entry 1")
            feed.append("human", "later\n")
            identity = Path(directory) / "feed.identities.sqlite3"
            self.assertGreater(identity.stat().st_size, 0)
            # A reader that merely opens the path (or an aborted rebuild) leaves a
            # zero-byte file behind. No table exists, so unpatched callers raise
            # "corrupt feed identity index: no such table: meta" on every append.
            identity.unlink()
            identity.write_bytes(b"")
            replayed = Feed(directory).append_runtime_once("mishe-tauftauf", first.body)
            self.assertEqual(replayed.sequence, first.sequence)
            later = Feed(directory).append("human", "after empty index\n")
            self.assertEqual(later.sequence, 3)
            self.assertEqual(len(parse_feed(feed.read_bytes())), 3)

    def test_empty_feed_index_rebuilds_instead_of_breaking_reads_and_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            feed.append("human", "first")
            feed.append("human", "second")
            index = Path(directory) / "feed.index.json"
            self.assertGreater(index.stat().st_size, 0)
            # An out-of-band reader that merely opens the path, or a rebuild aborted
            # before its atomic replace, leaves an unreadable checkpoint behind. The
            # canonical tape is intact, so reads and appends rebuild it instead of
            # failing closed forever with "corrupt feed index".
            for seeded, tail, appended in ((b"", 2, 3), (b'{"payload":', 3, 4)):
                index.unlink()
                index.write_bytes(seeded)
                self.assertEqual(Feed(directory).tail_sequence(), tail)
                self.assertEqual(Feed(directory).entries(start=tail, limit=1)[0].body, "after bad index" if tail == 3 else "second")
                self.assertEqual(Feed(directory).append("human", "after bad index").sequence, appended)
            self.assertEqual(len(parse_feed(feed.read_bytes())), 4)

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

    def test_reader_does_not_hold_the_shared_lock_while_it_parses(self):
        # The writer's exclusive lock must not wait for a reader's parse. A
        # reader that parsed under the shared lock would starve every writer
        # behind it, because a queued exclusive request is not granted while
        # readers keep re-acquiring the shared lock.
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(200):
                feed.append("human", f"event {number} " + "x" * 100)
            parsing = threading.Event()
            release = threading.Event()
            real_parse = parse_feed

            def blocking(data, **kwargs):
                if len(data) > 1000 and not parsing.is_set():
                    parsing.set()
                    release.wait(20)
                return real_parse(data, **kwargs)

            result = {}

            def read():
                try:
                    result["entries"] = Feed(directory).entries()
                except Exception as exc:  # pragma: no cover - surfaced by the assertions
                    result["error"] = exc

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=blocking):
                worker = threading.Thread(target=read)
                worker.start()
                self.assertTrue(parsing.wait(20), "the reader never reached the parse")
                acquired = False
                with feed.path.open("rb") as writer:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        try:
                            fcntl.flock(writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            acquired = True
                            break
                        except OSError:
                            time.sleep(0.02)
                    if acquired:
                        fcntl.flock(writer.fileno(), fcntl.LOCK_UN)
                self.assertTrue(acquired, "the reader held the shared lock while it parsed")
                release.set()
                worker.join(30)
            self.assertNotIn("error", result)
            self.assertEqual(len(result["entries"]), 200)

    def test_reader_returns_the_snapshot_it_captured_while_a_writer_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(200):
                feed.append("human", f"event {number} " + "x" * 100)
            parsing = threading.Event()
            release = threading.Event()
            real_parse = parse_feed

            def blocking(data, **kwargs):
                if len(data) > 1000 and not parsing.is_set():
                    parsing.set()
                    release.wait(20)
                return real_parse(data, **kwargs)

            result = {}

            def read():
                try:
                    result["entries"] = Feed(directory).entries()
                except Exception as exc:  # pragma: no cover - surfaced by the assertions
                    result["error"] = exc

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=blocking):
                worker = threading.Thread(target=read)
                worker.start()
                self.assertTrue(parsing.wait(20), "the reader never reached the parse")
                appended = feed.append("human", "appended while the reader parsed")
                release.set()
                worker.join(30)
            self.assertNotIn("error", result)
            self.assertEqual(len(result["entries"]), 200,
                             "the reader must return the revision it captured")
            self.assertNotIn("appended while the reader parsed",
                             [entry.body for entry in result["entries"]])
            self.assertEqual(len(Feed(directory).entries()), 201)
            self.assertEqual(Feed(directory).tail_sequence(), appended.sequence)

    def test_rebuilt_checkpoint_does_not_regress_a_newer_writer_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(200):
                feed.append("human", f"event {number} " + "x" * 100)
            feed.index_path.unlink()
            parsing = threading.Event()
            release = threading.Event()
            real_parse = parse_feed

            def blocking(data, **kwargs):
                if len(data) > 1000 and not parsing.is_set():
                    parsing.set()
                    release.wait(20)
                return real_parse(data, **kwargs)

            result = {}

            def read():
                try:
                    result["entries"] = Feed(directory).entries()
                except Exception as exc:  # pragma: no cover - surfaced by the assertions
                    result["error"] = exc

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=blocking):
                worker = threading.Thread(target=read)
                worker.start()
                self.assertTrue(parsing.wait(20), "the reader never reached the rebuild")
                feed.append("human", "appended while the reader rebuilt")
                release.set()
                worker.join(30)
            self.assertNotIn("error", result)
            self.assertEqual(len(result["entries"]), 200)
            checkpoint = json.loads(feed.index_path.read_text(encoding="utf-8"))["payload"]
            self.assertEqual(checkpoint["sequence"], 201,
                             "the rebuilt checkpoint must not regress the writer's revision")

    def test_reader_does_not_publish_a_checkpoint_for_bytes_it_never_read(self):
        # The reader rebuilds its checkpoint after it released the tape lock, so
        # the tape can grow while it parses. It must not install a checkpoint
        # that describes the whole file, because its index was built from the
        # bytes it captured, before that growth.
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(200):
                feed.append("human", f"event {number} " + "x" * 100)
            feed.index_path.unlink()
            parsing = threading.Event()
            release = threading.Event()
            real_parse = parse_feed

            def blocking(data, **kwargs):
                if len(data) > 1000 and not parsing.is_set():
                    parsing.set()
                    release.wait(20)
                return real_parse(data, **kwargs)

            result = {}

            def read():
                try:
                    result["entries"] = Feed(directory).entries()
                except Exception as exc:  # pragma: no cover - surfaced by the assertions
                    result["error"] = exc

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=blocking):
                worker = threading.Thread(target=read)
                worker.start()
                self.assertTrue(parsing.wait(20), "the reader never reached the rebuild")
                feed.append("human", "appended while the reader rebuilt")
                # Leave the tape grown and the checkpoint absent, so the
                # reader's own publish is the only candidate.
                feed.index_path.unlink()
                release.set()
                worker.join(30)
            self.assertNotIn("error", result)
            self.assertEqual(len(result["entries"]), 200)
            if feed.index_path.exists():
                checkpoint = json.loads(feed.index_path.read_text(encoding="utf-8"))["payload"]
                self.assertEqual(checkpoint["sequence"], 200,
                                 "a published checkpoint must describe exactly the bytes that were parsed")
            self.assertEqual(len(Feed(directory).entries()), 201,
                             "the next reader must still see every appended frame")

    def test_reader_fails_closed_when_the_checkpoint_corrupts_while_it_parses(self):
        # A corrupt checkpoint is a corruption, not an absent one. The reader
        # that rebuilt outside the tape lock must not silently replace it.
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            for number in range(200):
                feed.append("human", f"event {number} " + "x" * 100)
            feed.index_path.unlink()
            parsing = threading.Event()
            release = threading.Event()
            real_parse = parse_feed

            def blocking(data, **kwargs):
                if len(data) > 1000 and not parsing.is_set():
                    parsing.set()
                    release.wait(20)
                return real_parse(data, **kwargs)

            result = {}

            def read():
                try:
                    result["entries"] = Feed(directory).entries()
                except Exception as exc:  # pragma: no cover - surfaced by the assertions
                    result["error"] = exc

            with patch("mishe_tauftauf.feed.parse_feed", side_effect=blocking):
                worker = threading.Thread(target=read)
                worker.start()
                self.assertTrue(parsing.wait(20), "the reader never reached the rebuild")
                feed.index_path.write_text('{"version":99}', encoding="utf-8")
                release.set()
                worker.join(30)
            self.assertIn("error", result, "a corrupt checkpoint must not be replaced silently")
            self.assertIsInstance(result.get("error"), FeedError)
            self.assertEqual(feed.index_path.read_text(encoding="utf-8"), '{"version":99}',
                             "the corrupt checkpoint must survive the failed read")

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

    def test_streaming_reader_yields_the_same_entries_as_the_whole_tape_read(self):
        """The streaming reader is a reader, not a second dialect.

        The evidence-binding gate's peak was the tape parse: the whole-tape
        read kept every entry in memory at once, and the peak grew with the
        tape. The streaming reader reads the same tape without holding it, so
        the two must agree on every entry.
        """
        with tempfile.TemporaryDirectory() as directory:
            feed = Feed(directory)
            bodies = [
                "one line\n",
                "two\nlines\n",
                "trailing without a newline",
                "carriage\rreturn inside\n",
                "héllo → ✓ multibyte\n",
            ]
            for body in bodies:
                feed.append("genome", body)
            path = Path(directory) / "chat.log"
            whole = parse_feed(path.read_bytes(), home=directory)
            streamed = list(feed_module.iter_feed_file(path, home=directory))
            self.assertEqual(whole, streamed)
            self.assertTrue(whole)

    def test_streaming_reader_reports_the_same_faults_as_the_whole_tape_read(self):
        """A tape that faults faults the same way, with the same reason.

        A malformed tape must read as unreadable rather than as a shorter
        tape, so the streaming reader has to fail where the whole-tape read
        fails and say why. A body carrying a separator that ends a line is
        one of these faults: the framed body no longer reads as a framed
        body, and both readers must say so.
        """
        faults = {
            "malformed header": b"not a header\n",
            "non-contiguous": (b"v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::\n"
                               b"    | a\n    .\n"
                               b"v1 00000000000000000003 2026-10-10T15:51:57.625536Z seed ::\n"
                               b"    | b\n    .\n"),
            "unterminated": b"v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::\n    | a\n",
            "malformed body framing": (b"v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::\n"
                                       b"    | a\n  bad\n    .\n"),
            "empty body": b"v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::\n    .\n",
            "vertical tab in a body": (b"v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::\n"
                                       b"    | a\vb\n    .\n"),
            "unicode separator in a body": (b"v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::\n"
                                            b"    | a\xe2\x80\xa8b\n    .\n"),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.log"
            for reason, data in faults.items():
                path.write_bytes(data)
                with self.assertRaises(FeedError) as whole_error:
                    parse_feed(data)
                with self.assertRaises(FeedError) as streamed_error:
                    list(feed_module.iter_feed_file(path, home=directory))
                self.assertEqual(str(whole_error.exception), str(streamed_error.exception),
                                 f"the streaming reader must fault the same way: {reason}")

    def test_streaming_reader_does_not_hold_the_tape(self):
        """The streaming reader's memory does not grow with the tape.

        The gate's peak is what the plant's live observer pays, and the tape
        grows with every message. The whole-tape read keeps every entry at
        once, so its cost grows with the tape; the streaming reader holds one
        chunk and one entry, so its cost does not. The property is measured
        against the whole-tape read on the same tape, because that is the
        cost the reader is there to avoid.
        """
        import tracemalloc
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.log"
            body = "x" * 16384
            with path.open("w", encoding="utf-8") as handle:
                for sequence in range(1, 401):
                    handle.write(f"v1 {sequence:020d} 2026-10-10T15:51:56.625536Z seed ::\n")
                    handle.write(f"    | {body}\n    .\n")
            tape_bytes = path.stat().st_size

            tracemalloc.start()
            try:
                whole = parse_feed(path.read_bytes(), home=directory)
                _current, whole_peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            tracemalloc.start()
            try:
                count = sum(1 for _entry in feed_module.iter_feed_file(path, home=directory))
                _current, streamed_peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(len(whole), 400)
            self.assertEqual(count, 400)
            # The streaming reader must cost a fraction of the whole-tape
            # read on the same tape, and must not cost the tape itself.
            self.assertLess(streamed_peak, whole_peak // 2)
            self.assertLess(streamed_peak, tape_bytes)

    def test_streaming_reader_sees_a_torn_tail_as_a_fault(self):
        """A half-written entry is a fault, not a shorter tape.

        A writer appends under an exclusive lock, so a reader that captures
        the tape's length under the shared lock sees whole entries. A tail
        that is torn anyway must read as unreadable.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.log"
            path.write_bytes(b"v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::\n"
                             b"    | half an entry")
            with self.assertRaises(FeedError):
                list(feed_module.iter_feed_file(path, home=directory))


if __name__ == "__main__":
    unittest.main()
