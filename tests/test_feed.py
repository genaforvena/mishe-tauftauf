from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from mishe_tauftauf.feed import Feed, FeedError


def append_worker(home: str, number: int) -> None:
    Feed(home).append(f"worker/{number}", f"line {number}\nsecond\n" if number % 2 else f"line {number}\nsecond")


class FeedTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
