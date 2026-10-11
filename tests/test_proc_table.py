"""The process-table adapter decodes columns and refuses to guess.

``ps -eo pid=,ppid=`` is external text. The adapter is the only place that reads
it, so the malformed and truncated shapes it can take are pinned here rather
than discovered by a caller that then reaps the wrong process.
"""
from __future__ import annotations

import os
import subprocess
import sys
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

    def test_pane_windows_decode_window_index_dead_pid(self):
        self.assertEqual(
            proc_table.pane_windows("health 0 0 4242\nhealth 1 0 4243\ntail 0 1 4244\n"),
            [("health", "0", "0", "4242"), ("health", "1", "0", "4243"), ("tail", "0", "1", "4244")])

    def test_pane_windows_skip_junk_truncated_and_non_decimal_pid(self):
        self.assertEqual(
            proc_table.pane_windows("health 0 0\nhealth 0 0 x\n\njunk row here now\n"),
            [])

    def test_pane_environ_pythonpath_splits_nul_fields(self):
        self.assertEqual(
            proc_table.pane_environ_pythonpath(b"HOME=/srv\0PYTHONPATH=/srv/pin/src\0TERM=xterm\0"),
            ["/srv/pin/src"])
        self.assertEqual(
            proc_table.pane_environ_pythonpath(b"PYTHONPATH=/a:/b\0\0"), ["/a", "/b"])
        self.assertEqual(proc_table.pane_environ_pythonpath(b"HOME=/srv\0TERM=xterm\0"), [])

    def test_pane_environ_pythonpath_replaces_undecodable_bytes(self):
        self.assertEqual(
            proc_table.pane_environ_pythonpath(b"PYTHONPATH=/srv/\xff/src\0"), ["/srv/\ufffd/src"])

    def test_read_pane_pythonpath_reads_a_live_process_environment(self):
        # Wait for the child to exec before reading /proc/<pid>/environ: at fork
        # the kernel still reports the parent's environment, so an immediate read
        # is a race, not evidence.
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.stdout.write('ready'); sys.stdout.flush(); time.sleep(5)"],
            stdout=subprocess.PIPE, env={**os.environ, "PYTHONPATH": "/srv/live/src"})
        try:
            self.assertIsNotNone(proc.stdout)
            self.assertEqual(proc.stdout.read(5), b"ready")
            self.assertEqual(proc_table.read_pane_pythonpath(str(proc.pid)), ["/srv/live/src"])
        finally:
            proc.kill()
            proc.wait()

    def test_read_pane_pythonpath_returns_empty_for_an_unreadable_pid(self):
        self.assertEqual(proc_table.read_pane_pythonpath(""), [])
        self.assertEqual(proc_table.read_pane_pythonpath("not-a-pid"), [])

    def test_pane_root_drops_the_src_leaf_only(self):
        self.assertEqual(proc_table.pane_root("/srv/pin/src"), "/srv/pin")
        self.assertEqual(proc_table.pane_root("/srv/pin"), "/srv/pin")
        self.assertEqual(proc_table.pane_root("/srv/srcpad"), "/srv/srcpad")


if __name__ == "__main__":
    unittest.main()
