import calendar
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("htr", ROOT / "checks" / "human-typed-relayed.py")
htr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(htr)

NOW = "2026-11-19T19:00:00Z"


def entry(text, at=NOW, origin_kind="human", prompt="typed", turn="human", is_meta=False,
          sidechain=False, content=None, typ="user"):
    msg = {"role": "user", "content": content if content is not None else text}
    return json.dumps({
        "type": typ,
        "origin": {"kind": origin_kind},
        "turnOrigin": turn,
        "promptSource": prompt,
        "isMeta": is_meta,
        "isSidechain": sidechain,
        "timestamp": at,
        "uuid": f"uuid-{at}-{text[:6]}",
        "message": msg,
    }) + "\n"


def assistant(text):
    return json.dumps({"type": "assistant", "turnOrigin": "assistant", "promptSource": None,
                       "message": {"role": "assistant", "content": text}}) + "\n"


class HumanTypedRelayedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "site"
        self.repo = self.tmp / "repo"
        self.sessions = self.tmp / "projects" / htr.slug_for_repo(self.repo)
        self.sessions.mkdir(parents=True)
        self.now = calendar.timegm(time.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ"))
        self.recorder = self.tmp / "fake-mishe"
        self.recorded = self.tmp / "recorded.jsonl"
        self.recorder.write_text(
            f"#!{os.sys.executable}\n"
            "import json, sys\n"
            f"open({str(self.recorded)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        )
        self.recorder.chmod(0o755)

    def cursor(self):
        p = self.home / "human-typed" / "cursor.json"
        return json.loads(p.read_text()) if p.exists() else None

    def run_main(self, relay=False):
        argv = ["--home", str(self.home), "--repo", str(self.repo),
                "--sessions", str(self.sessions)] + (["--relay"] if relay else [])
        return htr.main(argv, now=self.now, mishe_bin=str(self.recorder))

    def recorded_calls(self):
        if not self.recorded.exists():
            return []
        return [json.loads(x) for x in self.recorded.read_text().splitlines()]

    def test_slug_matches_claude_projects_convention(self):
        self.assertEqual(htr.slug_for_repo(Path("/x/y/z")), "-x-y-z")
        self.assertTrue(htr.slug_for_repo(self.repo).startswith("-"))

    def test_first_run_registers_transcripts_at_end_and_replays_nothing(self):
        f = self.sessions / "a.jsonl"
        f.write_text(entry("old decision"))
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.cursor(), {"a.jsonl": f.stat().st_size})
        self.assertEqual(self.recorded_calls(), [])

    def test_new_human_line_is_reported_and_red_when_old(self):
        f = self.sessions / "a.jsonl"
        f.write_text(entry("old"))
        self.run_main()
        with f.open("ab") as fh:
            fh.write(entry("new decision", at="2026-11-19T18:40:00Z").encode())
        self.assertEqual(self.run_main(), 2)
        self.assertEqual(self.recorded_calls(), [])

    def test_relay_appends_once_and_advances_cursor(self):
        f = self.sessions / "a.jsonl"
        base = entry("old").encode()
        f.write_bytes(base)
        self.run_main()
        fresh = entry("one").encode() + entry("two").encode() + assistant("reply").encode()
        with f.open("ab") as fh:
            fh.write(fresh)
        self.assertEqual(self.run_main(relay=True), 0)
        calls = self.recorded_calls()
        self.assertEqual(len(calls), 2)
        self.assertIn("one", calls[0][-1])
        self.assertIn("two", calls[1][-1])
        self.assertLess(self.cursor()["a.jsonl"], f.stat().st_size)  # assistant tail not consumed yet
        self.assertEqual(self.run_main(), 0)  # tail consumed, nothing pending
        self.assertEqual(self.cursor()["a.jsonl"], f.stat().st_size)
        self.assertEqual(len(self.recorded_calls()), 2)

    def test_nonhuman_and_nontyped_lines_are_never_relayed(self):
        f = self.sessions / "a.jsonl"
        f.write_text(entry("old"))
        self.run_main()
        noise = (assistant("work") + entry("pasted", prompt="pasted") +
                 entry("meta", is_meta=True) + entry("sidechain", sidechain=True) +
                 entry("toolresult", origin_kind="tool", turn="tool") + "not json\n")
        with f.open("ab") as fh:
            fh.write(noise.encode())
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.recorded_calls(), [])
        self.assertEqual(self.cursor()["a.jsonl"], f.stat().st_size)

    def test_list_content_blocks_are_joined(self):
        f = self.sessions / "a.jsonl"
        f.write_text(entry("old"))
        self.run_main()
        content = [{"type": "text", "text": "first"}, {"type": "image", "source": "x"},
                   {"type": "text", "text": "second"}]
        with f.open("ab") as fh:
            fh.write(entry("", content=content).encode())
        self.assertEqual(self.run_main(relay=True), 0)
        self.assertIn("first\nsecond", self.recorded_calls()[-1][-1])

    def test_truncated_session_resets_cursor_without_unknown(self):
        f = self.sessions / "a.jsonl"
        f.write_text(entry("a long original session decision text"))
        self.run_main()
        f.write_text(entry("x"))  # rewritten smaller than the first-run cursor
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.cursor()["a.jsonl"], f.stat().st_size)

    def test_new_transcript_file_registered_at_end(self):
        (self.sessions / "a.jsonl").write_text(entry("old"))
        self.run_main()
        (self.sessions / "b.jsonl").write_text(entry("from a new session"))
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.recorded_calls(), [])
        # then its next typed line is caught
        f = self.sessions / "b.jsonl"
        with f.open("ab") as fh:
            fh.write(entry("caught", at="2026-11-19T18:30:00Z").encode())
        self.assertEqual(self.run_main(), 2)

    def test_missing_sessions_dir_is_unknown(self):
        argv = ["--home", str(self.home), "--repo", str(self.repo),
                "--sessions", str(self.tmp / "nowhere")]
        self.assertEqual(htr.main(argv, now=self.now, mishe_bin="x"), 3)

    def test_corrupt_cursor_is_unknown(self):
        (self.sessions / "a.jsonl").write_text(entry("old"))
        self.run_main()
        (self.home / "human-typed" / "cursor.json").write_text("{nope")
        self.assertEqual(self.run_main(), 3)

    def test_relay_failure_keeps_cursor_at_last_relayed_line(self):
        fails = self.tmp / "failing-mishe"
        fails.write_text(f"#!{os.sys.executable}\nimport sys\nsys.exit(1)\n")
        fails.chmod(0o755)
        f = self.sessions / "a.jsonl"
        f.write_text(entry("old"))
        self.run_main()
        with f.open("ab") as fh:
            fh.write((entry("one") + entry("two")).encode())
        argv = ["--home", str(self.home), "--repo", str(self.repo),
                "--sessions", str(self.sessions), "--relay"]
        self.assertEqual(htr.main(argv, now=self.now, mishe_bin=str(fails)), 2)
        # no cursor write happened: both lines stay pending
        self.assertLess(self.cursor()["a.jsonl"], f.stat().st_size)


if __name__ == "__main__":
    unittest.main()
