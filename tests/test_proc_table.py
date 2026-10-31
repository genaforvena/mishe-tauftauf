"""The process-table adapter decodes columns and refuses to guess.

``ps -eo pid=,ppid=`` is external text. The adapter is the only place that reads
it, so the malformed and truncated shapes it can take are pinned here rather
than discovered by a caller that then reaps the wrong process.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
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


    def test_pane_pids_accepts_text_output(self):
        self.assertEqual(proc_table.pane_pids("4242\njunk\n4243\n"), [4242, 4243])

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

    def test_mind_pane_records_returns_only_pane_1(self):
        self.assertEqual(
            proc_table.mind_pane_records(
                "mishe-tauftauf|senses|4242|1\n"
                "mishe-tauftauf|senses|4243|2\n"
                "android-body|senses|4244|1\n"),
            [("mishe-tauftauf", "senses", 4242), ("android-body", "senses", 4244)])

    def test_mind_pane_records_skip_junk(self):
        self.assertEqual(
            proc_table.mind_pane_records("mishe-tauftauf|senses|4242\n"
                                         "mishe-tauftauf|senses|x|1\n"
                                         "mishe-tauftauf|senses|4243|2\n\n"),
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

    def test_window_names_decode_one_name_per_line(self):
        self.assertEqual(
            proc_table.window_names(b"genome\nledger\ntail\n"),
            ["genome", "ledger", "tail"])

    def test_window_names_accepts_text_output(self):
        self.assertEqual(proc_table.window_names("genome\nledger\n"), ["genome", "ledger"])

    def test_window_names_skips_blank_lines(self):
        self.assertEqual(proc_table.window_names(b"genome\n\n   \nledger\n"), ["genome", "ledger"])


    def test_pane_field_decodes_one_value_or_empty(self):
        self.assertEqual(proc_table.pane_field(b"4242\n"), "4242")
        self.assertEqual(proc_table.pane_field("  0  "), "0")
        self.assertEqual(proc_table.pane_field(b""), "")

    def test_pane_field_rejects_extra_rows_and_undecodable_values(self):
        for raw in (b"one\ntwo\n", b"\xff"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                proc_table.pane_field(raw)

    def test_launch_argv_preserves_quoted_arguments(self):
        self.assertEqual(proc_table.launch_argv(
            b"env 'PYTHONPATH=/srv/a b/src' /site/minds/genome\n"),
            ["env", "PYTHONPATH=/srv/a b/src", "/site/minds/genome"])

    def test_launch_argv_rejects_empty_malformed_or_undecodable_commands(self):
        for raw in (b"", b"env 'unfinished", b"\xff"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                proc_table.launch_argv(raw)

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

    def test_read_cgroup_unit_returns_the_owning_systemd_unit(self):
        # Read this very test process: its cgroup is always readable, and the
        # decoder must hand back exactly the last path component the kernel
        # wrote, whatever slice it names.
        unit = proc_table.read_cgroup_unit(str(os.getpid()))
        self.assertTrue(unit)
        self.assertNotIn("/", unit)

    def test_read_cgroup_unit_empty_for_unreadable_pid(self):
        self.assertEqual(proc_table.read_cgroup_unit(""), "")
        self.assertEqual(proc_table.read_cgroup_unit("not-a-pid"), "")
        self.assertEqual(proc_table.read_cgroup_unit("999999999"), "")

    def test_belongs_to_session_accepts_both_plant_keeper_shapes(self):
        self.assertTrue(proc_table.belongs_to_session(
            "mishe-self-development-current-session.scope",
            "mishe-self-development-current"))
        self.assertTrue(proc_table.belongs_to_session(
            "mishe-self-development-current-session-9f3a2b1c4d5e6f708192a3b4c5d6e7f8.service",
            "mishe-self-development-current"))

    def test_belongs_to_session_rejects_a_foreign_test_keeper(self):
        # A finished test's keeper shares the shared socket while nothing
        # restarts it; a same-prefix session must not be mistaken for the
        # plant's own keeper.
        self.assertFalse(proc_table.belongs_to_session(
            "mishe-self-development-current-extra-session-1.service",
            "mishe-self-development-current"))
        self.assertFalse(proc_table.belongs_to_session(
            "mishe-tauftauf-test-2052154-2e8e4ec20b-session-69098cb50e994a759167cd47c622babc.service",
            "mishe-self-development-current"))
        self.assertFalse(proc_table.belongs_to_session(
            "android-body-session.scope", "mishe-self-development-current"))
        self.assertFalse(proc_table.belongs_to_session("", "mishe-self-development-current"))
        self.assertFalse(proc_table.belongs_to_session("x-session.scope", ""))

    def test_belongs_to_session_rejects_malformed_same_prefix_units(self):
        # A same-prefix unit matching neither documented shape must be
        # rejected; the old prefix check accepted these and could hide drift.
        session = "mishe-self-development-current"
        self.assertFalse(proc_table.belongs_to_session(
            session + "-sessionx.scope", session))
        self.assertFalse(proc_table.belongs_to_session(
            session + "-session-foreign.scope", session))
        self.assertFalse(proc_table.belongs_to_session(
            session + "-session-foreign.service", session))
        self.assertFalse(proc_table.belongs_to_session(
            session + "-session-.service", session))

    def test_belongs_to_session_rejects_non_32_hex_service_suffix(self):
        # uuid4().hex is exactly 32 lowercase hex chars; shorter, longer,
        # or non-hex suffixes are not producer-shaped units.
        session = "mishe-self-development-current"
        self.assertFalse(proc_table.belongs_to_session(
            session + "-session-9f3a.service", session))          # short hex
        self.assertFalse(proc_table.belongs_to_session(
            session + "-session-9f3a2b1c4d5e6f708192a3b4c5d6e7f80aa.service", session))  # long hex
        self.assertFalse(proc_table.belongs_to_session(
            session + "-session-9f3a2b1c4d5e6f708192a3b4c5d6e7f8g.service", session))  # non-hex
        self.assertFalse(proc_table.belongs_to_session(
            session + "-session-9F3A2B1C4D5E6F708192A3B4C5D6E7F80.service", session))  # uppercase


    def test_git_reflog_entries_pair_each_sha_with_its_message(self):
        raw = ("a" * 40 + " reset: moving to HEAD~1\n"
               + "b" * 40 + " commit: senses: stop reporting checked drift rows\n")
        self.assertEqual(proc_table.git_reflog_entries(raw),
                         [("a" * 40, "reset: moving to HEAD~1"),
                          ("b" * 40, "commit: senses: stop reporting checked drift rows")])

    def test_git_reflog_entries_skip_junk_and_messageless_lines(self):
        self.assertEqual(proc_table.git_reflog_entries("junk line\n" + "c" * 40 + "   \n"), [])

    def test_git_path_names_keeps_only_nonblank_paths(self):
        self.assertEqual(proc_table.git_path_names("src/a.py\n\ntests/test_a.py\n"),
                         ["src/a.py", "tests/test_a.py"])
        self.assertEqual(proc_table.git_path_names(""), [])
class SandboxProcessScanTests(unittest.TestCase):
    """A synthetic /proc tree: the scan reads only what the fixture provides."""

    LIFELINE = "0::/user.slice/user-1000.slice/mishe-lifeline.slice/tmux-spawn-x.scope\n"
    PLANTS = "0::/user.slice/user-1000.slice/mishe-plants.slice/tmux-spawn-y.scope\n"

    def _proc(self, root, pid, cgroup=None, argv=b"", cwd=None):
        entry = root / str(pid)
        entry.mkdir()
        if cgroup is not None:
            (entry / "cgroup").write_text(cgroup)
        if argv is not None:
            (entry / "cmdline").write_bytes(argv)
        if cwd is not None:
            (entry / "cwd").symlink_to(cwd)
        return entry

    def test_argv_marker_inside_lifeline_is_a_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cwd = root / "home"
            cwd.mkdir()
            self._proc(root, 4001, self.LIFELINE,
                       b"tmux\0-S\0/tmp/pytest-of-mesh-home/pytest-205/mishe-test-tmux0/tmux-1000/default\0",
                       cwd=str(cwd))
            matches, unreadable = proc_table.scan_sandbox_processes(proc_root=root)
            self.assertEqual(matches, [{"pid": 4001, "cwd": str(cwd), "marker": "pytest-of-"}])
            self.assertEqual(unreadable, 0)

    def test_cwd_marker_matches_watcher_loops_without_argv_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox = root / "sandbox"
            sandbox.mkdir()
            self._proc(root, 4002, self.LIFELINE, b"python3\0-m\0mishe_tauftauf\0pain\0watch\0genome\0",
                       cwd=str(sandbox))
            matches, _ = proc_table.scan_sandbox_processes(proc_root=root)
            self.assertEqual(matches, [])
            # The sandbox path under a pytest tree is what the watcher's cwd names.
            sandbox2 = root / "pytest-of-mesh-home" / "pytest-205" / "application"
            sandbox2.mkdir(parents=True)
            self._proc(root, 4003, self.LIFELINE, b"python3\0-m\0mishe_tauftauf\0pain\0watch\0health\0",
                       cwd=str(sandbox2))
            matches, _ = proc_table.scan_sandbox_processes(proc_root=root)
            self.assertEqual([m["pid"] for m in matches], [4003])
            self.assertEqual(matches[0]["marker"], "pytest-of-")

    def test_marker_outside_the_slice_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._proc(root, 4004, self.PLANTS, b"tmux\0-S\0/tmp/pytest-of-x/mishe-test-tmux0/default\0")
            self._proc(root, 4005, None, b"tmux\0-S\0/tmp/pytest-of-x/mishe-test-tmux0/default\0")
            matches, unreadable = proc_table.scan_sandbox_processes(proc_root=root)
            self.assertEqual(matches, [])
            self.assertEqual(unreadable, 1)

    def test_unreadable_cwd_is_counted_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._proc(root, 4006, self.LIFELINE, b"python3\0-m\0health\0", cwd=None)
            matches, unreadable = proc_table.scan_sandbox_processes(proc_root=root)
            self.assertEqual(matches, [])
            self.assertEqual(unreadable, 1)

    def test_non_numeric_entries_are_skipped_and_missing_root_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "self").mkdir()
            self.assertEqual(proc_table.scan_sandbox_processes(proc_root=root), ([], 0))
            with self.assertRaises(OSError):
                proc_table.scan_sandbox_processes(proc_root=root / "absent")


if __name__ == "__main__":
    unittest.main()

    def test_read_process_comm_returns_empty_for_unreadable_pid(self):
        self.assertEqual(proc_table.read_process_comm(""), "")
        self.assertEqual(proc_table.read_process_comm("not-a-pid"), "")

    def test_is_tmux_server_rejects_non_tmux_wrappers(self):
        # rnsh wraps `tmux new-session -A` in its arguments: comm is rnsh,
        # not tmux, so it must not read as a server.
        import subprocess
        probe = subprocess.run(["pgrep", "-f", "tmux new-session"],
                               capture_output=True, timeout=2)
        if probe.returncode != 0:
            self.skipTest("no tmux-argument processes live")
        for pid_text in probe.stdout.decode().split():
            if not pid_text.isdigit():
                continue
            comm = proc_table.read_process_comm(pid_text)
            argv = proc_table.read_pane_cmdline(pid_text)
            expected = comm.startswith("tmux") and "new-session" in argv
            self.assertEqual(proc_table.is_tmux_server(pid_text), expected,
                             f"pid={pid_text} comm={comm!r}")
