"""The process-table adapter decodes columns and refuses to guess.

``ps -eo pid=,ppid=`` is external text. The adapter is the only place that reads
it, so the malformed and truncated shapes it can take are pinned here rather
than discovered by a caller that then reaps the wrong process.
"""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from mishe_tauftauf import proc_table


class ProcessTableTests(unittest.TestCase):
    def _decode(self, stdout: str):
        result = subprocess.CompletedProcess([], 0, stdout.encode(), b"")
        with mock.patch.object(proc_table.subprocess, "run", return_value=result):
            return proc_table.children_by_parent()

    def test_columns_decode_to_a_parent_map(self):
        self.assertEqual(
            self._decode("    1       0\n   42       1\n   43       1\n   44      42\n"),
            {0: [1], 1: [42, 43], 42: [44]})

    def test_junk_and_truncated_rows_are_skipped_not_guessed(self):
        self.assertEqual(
            self._decode("  junk line\n  12\n  13      12  extra\n  14      12\n"),
            {12: [14]})

    def test_unreadable_table_yields_no_relation(self):
        result = subprocess.CompletedProcess([], 1, b"", b"ps: not found")
        with mock.patch.object(proc_table.subprocess, "run", return_value=result):
            self.assertEqual(proc_table.children_by_parent(), {})

    def test_missing_ps_binary_yields_no_relation(self):
        with mock.patch.object(proc_table.subprocess, "run", side_effect=FileNotFoundError("ps")):
            self.assertEqual(proc_table.children_by_parent(), {})

    def test_undecodable_table_yields_no_relation(self):
        failure = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with mock.patch.object(proc_table.subprocess, "run", side_effect=failure):
            self.assertEqual(proc_table.children_by_parent(), {})

    def test_pane_pids_decode_and_skip_junk(self):
        self.assertEqual(proc_table.pane_pids(b"4242\n4243\n"), [4242, 4243])
        self.assertEqual(proc_table.pane_pids(b"4242\n\nnot-a-pid\n"), [4242])
        self.assertEqual(proc_table.pane_pids(b""), [])

    def test_pane_records_decode_session_window_pid(self):
        self.assertEqual(
            proc_table.pane_records("mishe-tauftauf|senses|4242\nandroid-body|senses|4243\n"),
            [("mishe-tauftauf", "senses", 4242), ("android-body", "senses", 4243)])

    def test_pane_records_skip_junk_not_guess_a_session(self):
        self.assertEqual(
            proc_table.pane_records("mishe-tauftauf|senses\nmishe-tauftauf|senses|x\n\n"),
            [])

    def test_pane_commands_decode_session_pid_command_and_cwd(self):
        self.assertEqual(
            proc_table.pane_commands(
                "mishe-tauftauf|4242|env PYTHONPATH=/srv/pin/src pain watch senses|/srv/site\n"
                "android-body|4243|env PYTHONPATH=/tmp/ci/src pain watch senses|/tmp/ci\n"),
            [("mishe-tauftauf", "4242",
              "env PYTHONPATH=/srv/pin/src pain watch senses", "/srv/site"),
             ("android-body", "4243",
              "env PYTHONPATH=/tmp/ci/src pain watch senses", "/tmp/ci")])

    def test_pane_commands_skip_junk_and_truncated_rows(self):
        # The separator is not quoted, so a command carrying one pushes the
        # cwd into the fourth field: only a truncated row or a pid that is not
        # a number is junk.
        self.assertEqual(
            proc_table.pane_commands(
                "mishe-tauftauf|4242|env A=1|/srv/site|/extra\n"
                "mishe-tauftauf|4243\n"
                "mishe-tauftauf|4244|env A=1|/srv/site\n"
                "mishe-tauftauf|x|env A=1|/srv/site\n\n"),
            [("mishe-tauftauf", "4242", "env A=1", "/srv/site|/extra"),
             ("mishe-tauftauf", "4244", "env A=1", "/srv/site")])


if __name__ == "__main__":
    unittest.main()
