import calendar
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hir", ROOT / "checks" / "human-inbound-relayed.py")
hir = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hir)


def stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class HumanInboundRelayedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "site"
        self.log = self.tmp / "voice-in.log"
        self.now = calendar.timegm(time.strptime("2026-11-09T23:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
        self.recorder = self.tmp / "fake-mishe"
        self.recorded = self.tmp / "recorded.jsonl"
        self.recorder.write_text(
            f"#!{os.sys.executable}\n"
            "import json, sys\n"
            f"open({str(self.recorded)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        )
        self.recorder.chmod(0o755)

    def write_log(self, lines):
        self.log.write_text("".join(f"{s}  {k}  {t}\n" for s, k, t in lines))

    def run_main(self, relay=False, voice=True):
        argv = ["--home", str(self.home), "--log", str(self.log)] + (["--relay"] if relay else [])
        return hir.main(argv, now=self.now, voice=lambda: voice, mishe_bin=str(self.recorder))

    def recorded_calls(self):
        if not self.recorded.exists():
            return []
        return [json.loads(x) for x in self.recorded.read_text().splitlines()]

    def test_parse_skips_malformed_lines(self):
        data = b"junk line\n2026-11-09T20:39:42Z  TEXT  hello\n"
        got = list(hir.pending(data, 0))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][2:], ("TEXT", "hello"))

    def test_first_run_dry_is_red_for_recent_line_and_writes_nothing(self):
        self.write_log([(stamp(self.now - 3600), "TEXT", "answer")])
        self.assertEqual(self.run_main(), 2)
        self.assertFalse((self.home / "human-inbound" / "cursor.json").exists())
        self.assertEqual(self.recorded_calls(), [])

    def test_first_run_does_not_replay_history_older_than_a_day(self):
        self.write_log([(stamp(self.now - 3 * 86400), "TEXT", "old")])
        self.assertEqual(self.run_main(relay=True), 0)
        self.assertEqual(self.recorded_calls(), [])
        self.assertTrue((self.home / "human-inbound" / "cursor.json").exists())

    def test_relay_appends_each_line_once_and_advances_cursor(self):
        self.write_log([(stamp(self.now - 600), "TEXT", "one"), (stamp(self.now - 60), "VOICE", "two")])
        self.assertEqual(self.run_main(relay=True), 0)
        calls = self.recorded_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:6], ["wall", "dm", "--source", "operator", "--to", "operator"])
        self.assertIn("one", calls[0][-1])
        self.assertIn("two", calls[1][-1])
        self.assertEqual(self.run_main(relay=True), 0)
        self.assertEqual(len(self.recorded_calls()), 2)

    def test_new_line_after_relay_is_relayed_next_run(self):
        self.write_log([(stamp(self.now - 600), "TEXT", "one")])
        self.run_main(relay=True)
        with self.log.open("a") as f:
            f.write(f"{stamp(self.now - 30)}  TEXT  three\n")
        self.assertEqual(self.run_main(relay=True), 0)
        self.assertEqual(len(self.recorded_calls()), 2)
        self.assertIn("three", self.recorded_calls()[-1][-1])

    def test_inactive_voice_receiver_is_unknown(self):
        self.write_log([(stamp(self.now - 60), "TEXT", "x")])
        self.assertEqual(self.run_main(voice=False), 3)

    def test_unreadable_log_is_unknown(self):
        self.assertEqual(hir.main(["--home", str(self.home), "--log", str(self.tmp / "missing.log")],
                                  now=self.now, voice=lambda: True, mishe_bin="x"), 3)


if __name__ == "__main__":
    unittest.main()
